# -*- coding: utf-8 -*-
"""
A set of pre-processing operations in the input dataset:
1. Interpolation of missing data
2. Indentification of saturated pixels
3. Estimation of noise level for each imaged voxel
4. Estimation of global time constants

@authors: agiovann epnev
"""

import numpy as np
from scipy.interpolate import griddata    
import tempfile 
import os
from joblib import Parallel,delayed
import shutil
#%%
def interpolate_missing_data(Y):
    """
    Interpolate any missing data using nearest neighbor interpolation.
    Missing data is identified as entries with values NaN
    Input:
    Y   np.ndarray (3D)
        movie, raw data in 3D format (d1 x d2 x T)
    
    Outputs:
    Y   np.ndarray (3D)
        movie, data with interpolated entries (d1 x d2 x T)
    coor list 
        list of interpolated coordinates
    """  
    coor=[];
    if np.any(np.isnan(Y)):
        raise Exception('The algorithm has not been tested with missing values (NaNs)')        
        for idx,row in enumerate(Y):
            nans=np.where(np.isnan(row))[0] 
            n_nans=np.where(~np.isnan(row))[0] 
            coor.append((idx,nans))
            Y[idx,nans]=np.interp(nans, n_nans, row[n_nans]) 
            
     
#    mis_data = np.isnan(Y)
#    coor = mis_data.nonzero()
#    ok_data = ~mis_data
#    coor_ok = ok_data.nonzero()
#    Yvals=[np.where(np.isnan(Y)) for row in Y]
#    
#    Yvals = griddata(coor_ok,Y[coor_ok],coor,method='nearest')
#    un_t = np.unique(coor[-1])
#    coorx = []
#    coory = []
#    Yvals = []
#    for i, unv in enumerate(un_t):
#        tm = np.where(coor[-1]==unv)
#        coorx.append(coor[0][tm].tolist())
#        coory.append(coor[1][tm].tolist())
#        Yt = Y[:,:,unv]
#        ok = ~np.isnan(Yt)
#        coor_ok = ok.nonzero()
#        ytemp = griddata(coor_ok,Yt[coor_ok],(coor[0][tm],coor[1][tm]),method='nearest')
#        Yvals.append(ytemp)
        
    return Y, coor

#%%
def find_unsaturated_pixels(Y, saturationValue = None, saturationThreshold = 0.9, saturationTime = 0.005):
    """Identifies the saturated pixels that are saturated and returns the ones that are not.
    A pixel is defined as saturated if its observed fluorescence is above 
    saturationThreshold*saturationValue at least saturationTime fraction of the time.
    Inputs:
    Y: np.ndarray
        input movie data, either 2D or 3D with time in the last axis
    saturationValue: scalar (optional)
        Saturation level, default value the lowest power of 2 larger than max(Y)
    saturationThreshold: scalar between 0 and 1 (optional)
        Fraction of saturationValue above which the fluorescence is considered to
        be in the saturated region. Default value 0.9
    saturationTime: scalar between 0 and 1 (optional)     
        Fraction of time that pixel needs to be in the saturated 
        region to be considered saturated. Default: 0.005
    
    Output:
    normalPixels:   nd.array
        list of unsaturated pixels
    
    """
    if saturationValue == None:
        saturationValue = np.power(2,np.ceil(np.log2(np.max(Y))))-1
        
    Ysat = (Y >= saturationThreshold*saturationValue)
    pix = np.mean(Ysat,Y.ndim-1).flatten('F') > saturationTime
    normalPixels = np.where(pix)
    
    return normalPixels

#%%
def get_noise_fft(Y, noise_range = [0.25,0.5], noise_method = 'logmexp'):
    """Estimate the noise level for each pixel by averaging the power spectral density.
    Inputs:
    Y: np.ndarray
    Input movie data with time in the last axis
    noise_range: np.ndarray [2 x 1] between 0 and 0.5
        Range of frequencies compared to Nyquist rate over which the power spectrum is averaged
        default: [0.25,0.5]
    noise method: string
        method of averaging the noise.
        Choices:
            'mean': Mean
            'median': Median
            'logmexp': Exponential of the mean of the logarithm of PSD (default)
    
    Output:
    sn: np.ndarray
        Noise level for each pixel
    """
    T = np.shape(Y)[-1]
    dims = len(np.shape(Y))
    ff = np.arange(0,0.5+1./T,1./T)
    ind1 = ff > noise_range[0]
    ind2 = ff <= noise_range[1]
    ind = np.logical_and(ind1,ind2)
    if dims > 1:
        xdft = np.fft.rfft(Y,axis=-1)
        psdx = (1./T)*abs(xdft)**2
        psdx[...,1:] *= 2
        sn = mean_psd(psdx[...,ind], method = noise_method)
        
    else:
        xdft = np.fliplr(rfft(Y))
        psdx = (1./T)*(xdft**2)
        psdx[1:] *=2
        sn = mean_psd(psdx[ind], method = noise_method)
    
    return sn     


def get_noise_fft_parallel(Y, n_processes=4,n_pixels_per_process=100, backend='threading', **kwargs):
    """parallel version of get_noise_fft.
    
    Params:
    -------
    Y: ndarray
        input movie (n_pixels x Time). Can be also memory mapped file. 
    
    n_processes: [optional] int
        number of processes/threads to use concurrently
        
            
    n_pixels_per_process: [optional] int 
        number of pixels to be simultaneously processed by each process
    
    backend: [optional] string
        the type of concurrency to be employed. 'threading' or 'multiprocessing'. In general
        threading should be used
    
    **kwargs: [optional] dict
        all the parameters passed to get_noise_fft     
    Returns:
    --------
    sn: ndarray(double)
        noise associated to each pixel
    
    """  
    
    folder = tempfile.mkdtemp()
    sn_name = os.path.join(folder, 'sn_s')            
    
    # Pre-allocate a writeable shared memory map as a container for the
    # results of the parallel computation
    sn_s = np.memmap(sn_name, dtype=np.float32,shape=Y.shape[0], mode='w+')   
    
    pixel_groups=range(0,Y.shape[0]-n_pixels_per_process+1,n_pixels_per_process)
    
    # Fork the worker processes to perform computation concurrently                    
    Parallel(n_jobs=n_processes, backend=backend)(delayed(fft_psd_parallel)(Y, sn_s, i, n_pixels_per_process, **kwargs)
                        for i in pixel_groups)   
                    

        
    # if n_pixels_per_process is not a multiple of Y.shape[0] run on remaining pixels   
    pixels_remaining= Y.shape[0] %  n_pixels_per_process  
    
    if pixels_remaining>0:  
        print "Running fft for remaining pixels:" + str(pixels_remaining)
        fft_psd_parallel(Y,sn_s,Y.shape[0]-pixels_remaining,pixels_remaining)    
    
    sn_s=np.array(sn_s)
    


    try:

        shutil.rmtree(folder)

    except:

        print("Failed to delete: " + folder)
        raise
        
    return sn_s    
#%%        

def fft_psd_parallel(Y,sn_s,i,num_pixels,**kwargs):
    """helper function to parallelize get_noise_fft
    
    Parameters:
    -----------
    Y: ndarray
        input movie (n_pixels x Time), can be also memory mapped file
        
    sn_s: ndarray (memory mapped)
        file where to store the results of computation. 
        
    i: int
        pixel index start
    num_pixels: int
        number of pixel to select starting from i
    
    **kwargs: dict
        arguments to be passed to get_noise_fft 
            
    
    """
    idxs=range(i,i+num_pixels)
    #sn_s[idxs]=get_noise_fft(Y[idxs], **kwargs)
    res=get_noise_fft(Y[idxs], **kwargs)
    sn_s[idxs]=res
    #print("[Worker %d] sn for row %d is %f" % (os.getpid(), i, sn_s[0]))
    

def mean_psd(y, method = 'logmexp'):
    """
    Averaging the PSD
    Inputs:
    y: np.ndarray
        PSD values
    method: string
        method of averaging the noise.
        Choices:
            'mean': Mean
            'median': Median
            'logmexp': Exponential of the mean of the logarithm of PSD (default)
    """
    
    if method == 'mean':
        mp = np.sqrt(np.mean(y/2,axis=-1))
    elif method == 'median':
        mp = np.sqrt(np.median(y/2,axis=-1))
    else:
        mp = np.log(y/2)
        mp = np.mean(mp,axis=-1)
        mp = np.exp(mp)
        mp = np.sqrt(mp)        
#        mp = np.sqrt(np.exp(np.mean(np.log(y/2),axis=-1)))
        
    return mp


#%%

def estimate_time_constant(Y, sn, p = None, lags = 5, include_noise = False, pixels = None):
    """ 
    Estimating global time constants for the dataset Y through the autocovariance function (optional).
    The function is no longer used in the standard setting of the algorithm since every trace has its own 
    time constant.
    Inputs:
    Y: np.ndarray (2D)
        input movie data with time in the last axis
    p: positive integer
        order of AR process, default: 2
    lags: positive integer
        number of lags in the past to consider for determining time constants. Default 5
    include_noise: Boolean
        Flag to include pre-estimated noise value when determining time constants. Default: False
    pixels: np.ndarray
        Restrict estimation to these pixels (e.g., remove saturated pixels). Default: All pixels
        
    Output:
    g:  np.ndarray (p x 1) 
        Discrete time constants
    """    
    if p is None:
        raise Exception("You need to define p")
        
    if pixels is None:
        pixels = np.arange(np.size(Y)/np.shape(Y)[-1])
    
    from scipy.linalg import toeplitz    
    
    npx = len(pixels)
    g = 0
    lags += p
    XC = np.zeros((npx,2*lags+1))
    for j in range(npx):
        XC[j,:] = np.squeeze(axcov(np.squeeze(Y[pixels[j],:]),lags))
        
    gv = np.zeros(npx*lags)
    if not include_noise:
        XC = XC[:,np.arange(lags-1,-1,-1)]
        lags -= p
        
    A = np.zeros((npx*lags,p))
    for i in range(npx):
        if not include_noise:
            A[i*lags+np.arange(lags),:] = toeplitz(np.squeeze(XC[i,np.arange(p-1,p+lags-1)]),np.squeeze(XC[i,np.arange(p-1,-1,-1)])) 
        else:
            A[i*lags+np.arange(lags),:] = toeplitz(np.squeeze(XC[i,lags+np.arange(lags)]),np.squeeze(XC[i,lags+np.arange(p)])) - (sn[i]**2)*np.eye(lags,p)
            gv[i*lags+np.arange(lags)] = np.squeeze(XC[i,lags+1:])
        
    if not include_noise:
        gv = XC[:,p:].T
        gv = np.squeeze(np.reshape(gv,(np.size(gv),1),order='F'))
        
    g = np.dot(np.linalg.pinv(A),gv)
    
    return g

def axcov(data, maxlag=5):
    """
    Compute the autocovariance of data at lag = -maxlag:0:maxlag
    Parameters
    ----------
    data : array
        Array containing fluorescence data
    maxlag : int
        Number of lags to use in autocovariance calculation
    Returns
    -------
    axcov : array
        Autocovariances computed from -maxlag:0:maxlag
    """
    
    data = data - np.mean(data)
    T = len(data)
    bins = np.size(data)
    xcov = fft(data, np.power(2, nextpow2(2 * bins - 1)))
    xcov = ifft(np.square(np.abs(xcov)))    
    xcov = np.concatenate([xcov[np.arange(xcov.size - maxlag, xcov.size)],
                           xcov[np.arange(0, maxlag + 1)]])
    #xcov = xcov/np.concatenate([np.arange(T-maxlag,T+1),np.arange(T-1,T-maxlag-1,-1)])
    return np.real(xcov/T)


#%%
def nextpow2(value):
    """
    Find exponent such that 2^exponent is equal to or greater than abs(value).
    Parameters
    ----------
    value : int
    Returns
    -------
    exponent : int
    """
    
    exponent = 0
    avalue = np.abs(value)
    while avalue > np.power(2, exponent):
        exponent += 1
    return exponent  

def preprocess_data(Y,  sn = None , n_processes=4, n_pixels_per_process=100,  noise_range = [0.25,0.5], noise_method = 'logmexp', compute_g=False,  p = 2, g = None,  lags = 5, include_noise = False, pixels = None):
    """
    Performs the pre-processing operations described above.
    """
    
    Y,coor=interpolate_missing_data(Y)            
    
    if sn is None:
        sn=get_noise_fft_parallel(Y, n_processes=n_processes,n_pixels_per_process=n_pixels_per_process, backend='threading', noise_range = noise_range, noise_method = noise_method)    
        #sn = get_noise_fft(Y, noise_range = noise_range, noise_method = noise_method)        
    
    if compute_g:
        g = estimate_time_constant(Y, sn, p = p, lags = lags, include_noise = include_noise, pixels = pixels)
    else:
        g=None
    
    return Y,sn,g    