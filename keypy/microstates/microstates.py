﻿# -*- coding: utf-8 -*-

##################################
#######   load packages   ########
##################################

from contextlib import closing
from math import sqrt

import h5py
import numpy

from keypy.microstates. microstates_helper import compute_gfp, gfp_peaks_indices, princomp_B

##################################
#######  run_microstates  ########
##################################

#################
# 1.) LOAD data from hdf5 & Create microstate_output dataset in hdf5   (TODO simplyfy confobj)
#################

def run_microstates(confobj, eeg_info_study_obj, inputhdf5, microstate_input = 'mstate1', microstate_output = 'microstate'):
    """
    Compute EEG microstates for each dataset in inputhdf5 of name microstate_input.

    Parameters
    ----------
    confobj : object of type MstConfiguration
        Contains the following attributes: subtract_column_mean_at_start (bool), debug (bool), use_gfp_peaks (bool), force_avgref (bool), set_gfp_all_1 (bool), use_smoothing (bool), gfp_type_smoothing (string),
        smoothing_window (int), use_fancy_peaks (bool), method_GFPpeak (string), original_nr_of_maps (int), seed_number (int), max_number_of_iterations (int), ERP (bool), correspondance_cutoff (double):
    eeg_info_study_obj : object of type EegInfo
        Contains the following attributes: nch (number of channels), tf (number of time frames per epoch), sf (sampling frequency), chlist (channel list)
    inputhdf5 : str
       Input path of the hdf5 file that contains the data to be processed, e.g. 'C:\\Users\\Patricia\\libs\\keypy\\example\\data\\input\\rawdata.hdf'
    microstate_input : str
        Name of the dataset in the hdf5 file that the microstate computation is based on ('mstate1' by default). 
    microstate_output : str
        Name of the output dataset that contains the EEG microstates ('microstate' by default). 
    """

    with closing( h5py.File(inputhdf5) ) as f:
        print 'Computing Microstates ....'
        for groupi in f['/'].keys():
            group_group = f['/%s' % (groupi)]
            for pti in group_group.keys():
                pt_group = f['/%s/%s' % (groupi, pti)]
                for condi in pt_group.keys():
                    cond_group = f['/%s/%s/%s' % (groupi, pti, condi)]
                    for runi in cond_group.keys():
                        run_group = f['/%s/%s/%s/%s' % (groupi, pti, condi, runi)]

                        try:
                            timeframe_channel_dset = f['/%s/%s/%s/%s/%s' % (groupi, pti, condi, runi, microstate_input)]
                        except:
                            print 'not found', groupi, pti, condi, runi, microstate_input
                            continue

                        print 'computing microstates', groupi, pti, condi, runi, microstate_input
                        path = '/%s/%s/%s/%s/%s' % (groupi, pti, condi, runi, microstate_input)  
                        eeg = f[path].value

                        if microstate_output in f['/%s/%s/%s/%s' % (groupi, pti, condi, runi)].keys():
                            print groupi, pti, condi, runi, 'microstates not recomputed'
                            continue         

                        if eeg_info_study_obj.nch !=eeg.shape[1]:
                            print 'Channel number in eeg_info_obj does not match EEG shape!'

                        #################
                        #6.) Preprocess eeg, compute gfp_peak_indices and gfp_curve based on confobj specifications          
                        #################

                        eeg, gfp_peak_indices, gfp_curve = mstate_preprocess(confobj, eeg, eeg_info_study_obj)


                        #################
                        #6.) Compute Microstate Model Maps            
                        #################
                                
                        b_model, b_ind, b_loading, best_fit, exp_var, exp_var_tot=find_mstates_maps(confobj, eeg_info_study_obj.nch, eeg, gfp_peak_indices, gfp_curve)


                        if not microstate_output in f['/%s/%s/%s/%s' % (groupi, pti, condi, runi)].keys():
                            microstate = f['/%s/%s/%s/%s' % (groupi, pti, condi, runi)].create_dataset(microstate_output, shape=(confobj.original_nr_of_maps,eeg.shape[1]))
                        else:
                            microstate = f['/%s/%s/%s/%s/%s' % (groupi, pti, condi, runi, microstate_output)]

                        # dataset.value retrieves the array into memory for read access only
                        # for writes you replace the whole array with [:], or subsets with slicing [1:3, 3:]
                        # the numpy shapes (on the right side of the assignment) need to match
                        microstate[:] = b_model

                        microstate.attrs['number of gfp peaks'] = len(b_ind)
                        microstate.attrs['explained variance of all gfp peaks'] = '%.2f' % (exp_var)
                        microstate.attrs['explained variance of all eeg timeframes'] = '%.2f' % (exp_var_tot) 

 

##################################
#######  mstate_preprocess  ########
##################################
def mstate_preprocess(confobj, eeg, eeg_info_study_obj):
    """
    Preprocesses (normalization option, GFP peak extraction, average referencing) EEG to prepare data for microstate computation.

    Parameters
    ----------
    confobj : object of type MstConfiguration
        Contains the following attributes: subtract_column_mean_at_start (bool), debug (bool), use_gfp_peaks (bool), force_avgref (bool), set_gfp_all_1 (bool), use_smoothing (bool), gfp_type_smoothing (string),
        smoothing_window (int), use_fancy_peaks (bool), method_GFPpeak (string), original_nr_of_maps (int), seed_number (int), max_number_of_iterations (int), ERP (bool), correspondance_cutoff (double):
    eeg : array
        Shape ntf*nch, conatains the EEG data the microstate analysis is to be computed on.
    eeg_info_study_obj : object of type EegInfo
        Contains the following attributes: nch (number of channels), tf (number of time frames per epoch), sf (sampling frequency), chlist (channel list)

    Returns
    -------
    eeg: array
        Preprocessed EEG.
    gfp_peak_indices: list
        List of indices of the EEG that qualify as global field power peaks.
    gfp_curve: 1D array
        Global field power for each time frame.
    """

    #################
    # 2.)a Subtract mean across columns (the mean map) [must be done for each 2 sec segment] (not done by default)
    #################              

    if confobj.subtract_column_mean_at_start:
        if confobj.debug:
            print 'column mean subtracted, does yield different results from published algorithm'
        eeg=subtract_column_mean_epochwise(eeg, eeg_info_study_obj.TF)
         
    #################   
    # 3.) COMPUTE GFP (ln1 or ln2)
    #################

    gfp_curve = compute_gfp(eeg, confobj.method_GFPpeak)
    if confobj.debug:
        print 'GFP Curve computed'

    #################
    #4.) Compute GFP Peaks (if the whole EEG is taken (use_gfp_peaks = False) it just returns the indices for the whole EEG
    #################

    gfp_peak_indices, gfp_curve = compute_gfp_peaks(gfp_curve, confobj.use_gfp_peaks, confobj.use_smoothing, confobj.gfp_type_smoothing, confobj.smoothing_window, confobj.use_fancy_peaks)

    #################
    # 2.) AVG REF
    #################

    if confobj.force_avgref:
        if confobj.debug:
            print 'Forced average reference'
        eeg=TK_norm(eeg, gfp_peak_indices, eeg_info_study_obj.nch)
                                     
    #################
    #5.) Set all Maps to GFP=1 (sets the maps for each timeframe to gfp=1)
    #################

    if confobj.set_gfp_all_1:
        print "GFP all maps set to 1"
        eeg = set_gfp_all_1(eeg, gfp_curve)

    return eeg, gfp_peak_indices, gfp_curve


##################################
#######  compute average ref version  ########
##################################
def TK_norm(eeg, gfp_peak_indices, nch):
    """
    Average referencing prior to microstate computation based on gfp_peak_indices (or whole EEG depending on confobj).

    Parameters
    ----------
    eeg : array
        Shape ntf*nch, conatains the EEG data the average referencing is to be computed on.
    gfp_peak_indices : list
        List of indices of the EEG that qualify as global field power peaks.
    nch : int
        Number of channels (TO DO: simplify function to derive nch based on EEG shape).

    Returns
    -------
    eeg: array
        Average-referenced EEG.
    """

    h=numpy.eye(nch)-1.0/nch                 
    eeg[gfp_peak_indices]=numpy.dot(eeg[gfp_peak_indices],h)

    return eeg


##################################
#######  set_gfp_all_1  ########
##################################
def set_gfp_all_1(eeg, gfp_curve):        
    """
    Normalizes EEG to set GFP to 1 for each time frame.

    Parameters
    ----------
    eeg : array
        Shape ntf*nch, conatains the EEG data the average referencing is to be computed on.
    gfp_curve : 1D array
        Global field power for each time frame.

    Returns
    -------
    eeg: array
        EEG with GFP set to 1.
    """
    for i in range(eeg.shape[0]):
        eeg[i,:] = eeg[i,:]/gfp_curve[i]
    return eeg


##################################
#######  compute_gfp_peaks  ########
##################################
def compute_gfp_peaks(gfp_curve, use_gfp_peaks, use_smoothing, gfp_type_smoothing, smoothing_window, use_fancy_peaks):
    """
    Computes GFP peaks from global field power curve.

    Parameters
    ----------
    gfp_curve : 1D array
        Global field power for each time frame.
    use_gfp_peaks : bool
        Option whether whole GFP peaks are used or not.
    use_smoothing : bool
        Option whether smoothing is to be applied to the GFP curve before peak computation or not.
    gfp_type_smoothing : {'hamming', 'hanning'}
        `hamming` : use hamming window to smooth
        `hanning` : use hanning window to smooth
    smoothing_window : int
		window for smoothing, e.g. 100.
    use_fancy_peaks : bool
        Whether a particular smoothing algorithm from scipy.signal.find_peaks_cwt is applied before peak computation or not.
        Reference: Bioinformatics (2006) 22 (17): 2059-2065. doi: 10.1093/bioinformatics/btl355 http://bioinformatics.oxfordjournals.org/content/22/17/2059.long)

    Returns
    -------
    gfp_peak_indices : list
        List of indices of the EEG that qualify as global field power peaks.
    gfp_curve : 1D array
        GFP curve after smoothing (if smoothing was applied).
    """
    if use_gfp_peaks:
        if use_smoothing:
            gfp_curve=gfp_smoothing(gfp_curve, gfp_type_smoothing, smoothing_window)
        if use_fancy_peaks:
            peakind = scipy.signal.find_peaks_cwt(gfp_curve, numpy.arange(1,10))
            gfp_peak_indices=numpy.asarray(peakind) #we would expect a peak at about each 50 ms
            gfp_curve = gfp_curve
        else:
            gfp_peak_indices=gfp_peaks_indices(gfp_curve) #we would expect a peak at about each 50 ms
            gfp_curve = gfp_curve
    else:
        gfp_peak_indices=numpy.array(range(len(gfp_curve)))   #when we take all maps, we still call the array gfp_peak_indices
        gfp_curve = gfp_curve
        print 'all maps used'

    return gfp_peak_indices, gfp_curve


##################################
#######  subtract_column_mean_epochwise  ########
##################################
def subtract_column_mean_epochwise(eeg, TF):
    """
    Normalizes EEG (subtracts column mean for each epoch)

    Parameters
    ----------
    eeg : array
        Shape ntf*nch, conatains the EEG data the average referencing is to be computed on.
    TF : int
        Number of time frames per epoch.

    Returns
    -------
    eeg : array
        Normalized EEG.
    """
    for i in range(len(eeg)/TF):
            epoch = eeg[i*TF:(i+1)*TF]
            epoch_mean_subtracted = (epoch-mean(epoch.T,axis=1))
            eeg[i*TF:(i+1)*TF,:] = epoch_mean_subtracted
    return eeg


##################################
#######  find mstates maps  ########
##################################
def find_mstates_maps(confobj, nch, eeg, gfp_peak_indices, gfp_curve):
    """
    Finds N-M number of Microstates from dataset based on all tfs or only gfp_peaks

    Parameters
    ----------
    confobj : object of type MstConfiguration
        object that defines the parameters for the microstate computation 
    nch : int
        number of channels from eeg_info_study_obj
    eeg : ndarray
        Array containing values for all time frames and channels.
    gfp_peak_indices : ndarray
        Array containing indices for all GFP peaks in eeg / all tfs if all tfs are used
    gfp_curve : array
		ntf length of GFP across all nch

    Returns
    -------
    b_model : array
        original_nr_of_maps x nch array containing the retrieved microstate maps
    b_ind : double

    b_loading : double

    best_fit : double
		highest mean correlation between the N microstates and the M GFP peak maps
    exp_var : double
		explained variance of all GFP peaks
    exp_var_tot : 
		explained variance of the whole EEG

    """ 
    #################
    # 0.) ###Configuration for Microstates.py
    #################
    
    #only necessary if we do not want to use the whole EEG but for example only a random selection of its gfp peaks to compute the microstates
    org_data = eeg
    if confobj.debug:
        print org_data.shape
    best_fit = 0
    #max_n refers to the maximal number of GFP peaks used for computation, here the default is all gfp peaks
    max_n = len(gfp_peak_indices)

    fixed_seed = confobj.fixed_seed
        
    #loop across runs
    for run in range(confobj.seed_number):
        if confobj.debug:
            print "-----------------"
            print "Seed_number", run
            print "-----------------"
        
        #Pick 4 random map indices based on all gfp peaks
        #fix sequence of seeds for testing
        if fixed_seed != None:
            numpy.random.seed(fixed_seed)
        random_map_indices = numpy.random.random_integers(0, len(gfp_peak_indices) - 1, (confobj.original_nr_of_maps,) )      
        
        #the first model is based on the above random selection    
        model = eeg[gfp_peak_indices[random_map_indices]]
        if confobj.debug:
            print 'random_map_indices', random_map_indices
                              
        #Computation of norm vector (set all to vector length 1)
        b=numpy.sum(numpy.abs(model)**2,axis=-1)**(1./2)
        
        #Divide all elements by the norm vector
        for col in range(nch):
            model[:,col]=model[:,col]/b
    
        #Some initialization for the attribution matrix (shape: number of global field power peaks x 1)
        #max_n: maximal number of gfp peaks used, by default all gfp_peaks
        o_ind= numpy.zeros((max_n))
        ind=numpy.ones((max_n))
        
        #Loop until the attribution matrix does not change anymore
        while numpy.allclose(o_ind, ind, rtol=1.0000000000000001e-05, atol=1e-08)==False:
            
            #Update attribution matrix from last loop
            o_ind   = ind
            if confobj.ERP:
                #Get signed covariance matrix for ERP
                covm= numpy.dot(eeg[gfp_peak_indices],model.T)  
                       
            else:
                #Get unsigned covariance matrix for EEG
                covm= abs(numpy.dot(eeg[gfp_peak_indices],model.T))
          
            #Look for the best fit (gives maximum value of axis = 1 of covm)
            ind = covm.argmax(axis=1)
            
            #Compute PC1
            #uses function "princomp_B" from keypy.ressources
            for mm_index in range(confobj.original_nr_of_maps):
                P=numpy.array(eeg[gfp_peak_indices[ind==mm_index],:])   
                coeff = princomp_B(P,1)
                model[mm_index,:] = coeff.ravel()
                            
            #avg ref and norm        
            b=numpy.sum(numpy.abs(model)**2,axis=-1)**(1./2)
        
            for col in range(nch):
                model[:,col]=model[:,col]/b
            
            #Get unsigned covariance matrix
            covm= numpy.dot(eeg[gfp_peak_indices],model.T)
                
            if confobj.ERP:
                #Look for the best fit
                ind = (covm).argmax(axis=1)
            else:
                #Look for the best fit
                ind = abs(covm).argmax(axis=1)               
        
        #Get the unsigned covariance
        covm=numpy.dot(org_data[gfp_peak_indices],model.T)
        covm_all=numpy.dot(org_data,model.T)
        
        if confobj.ERP:
            # Look for the best fit
            ind = covm.argmax(axis=1)	
            loading=covm.max(axis=1)
            #Indices for all timeframes
            #ind_all = covm_all.argmax(axis=1)	
            loading_all=covm_all.max(axis=1)
        else:
            # Look for the best fit
            ind = abs(covm).argmax(axis=1)
            loading=abs(covm).max(axis=1)
            #Indices for all timeframes
            #ind_all = abs(covm_all).argmax(axis=1)
            loading_all=abs(covm_all).max(axis=1)
          
        tot_fit = sum(loading)
        
        if tot_fit > best_fit:
            b_model=model
            b_ind=ind
            b_loading=loading/sqrt(nch)
            b_loading_all=loading_all/sqrt(nch)
            best_fit=tot_fit
            #exp var based on gfp peaks only
            exp_var=sum(b_loading)/sum(eeg[gfp_peak_indices].std(axis=1))
            #exp var based on all eeg timeframes
            exp_var_tot=sum(b_loading_all)/sum(eeg.std(axis=1))

    return b_model, b_ind, b_loading, best_fit, exp_var, exp_var_tot