"""A likelihood function using the FastPMRunner. Modified from the code at
https://github.com/Michalychforever/lss_montepython using this paper:
https://arxiv.org/abs/1909.05277

This code compares a measured 2-point fourier-space correlation function
(Multipoles of a redshift space power spectrum) from the BOSS galaxy survey to a theory model.
For the equations see Section 3.2 of https://arxiv.org/abs/1909.05277

I have removed all the 'theory model' parameters discussed at length
as the point of this is to explore numerical approximations.
"""

import os
import numpy as np
from montepython.likelihood_class import Likelihood_prior
from numpy.fft import fft, ifft , rfft, irfft , fftfreq
from numpy import exp, log, log10, cos, sin, pi, cosh, sinh , sqrt
from scipy.special import gamma,erf
from scipy import interpolate
from scipy.integrate import quad
import scipy.integrate as integrate
from scipy import special

class bao_fs_ngc_z1_marg(Likelihood_prior):

    # initialisation of the class is done within the parent Likelihood_prior. For
    # this case, it does not differ, actually, from the __init__ method in
    # Likelihood class.

    def __init__(self,path,data,command_line):
        """Initialize the function, loading data and other useful functions that can be precomputed"""

        Likelihood_prior.__init__(self,path,data,command_line)

        ## LOAD IN DATA

        self.k = np.zeros(self.ksize,'float64')
        self.Pk0 = np.zeros(self.ksize,'float64')
        self.Pk2 = np.zeros(self.ksize,'float64')
        self.alphas = np.zeros(2,'float64')

        self.cov = np.zeros(
            (2*self.ksize+2, 2*self.ksize+2), 'float64')

        # Load covariance matrix
        datafile = open(os.path.join(self.data_directory, self.covmat_file), 'r')
        for i in range(2*self.ksize+2):
            line = datafile.readline()
            while line.find('#') != -1:
                line = datafile.readline()
            for j in range(2*self.ksize+2):
                self.cov[i,j] = float(line.split()[j])
        datafile.close()

        # Load unreconstructed power spectrum
        datafile = open(os.path.join(self.data_directory, self.measurements_file), 'r')
        for i in range(self.ksize):
            line = datafile.readline()
            while line.find('#') != -1:
                line = datafile.readline()
            self.k[i] = float(line.split()[0])
            self.Pk0[i] = float(line.split()[1])
            self.Pk2[i] = float(line.split()[2])
        datafile.close()

        ## LOAD OTHER USEFUL FUNCTIONS
        self.Nmax=128
        self.W0 = np.zeros((self.Nmax,1))
        self.W2 = np.zeros((self.Nmax,1))
        self.W4 = np.zeros((self.Nmax,1))
        datafile = open(os.path.join(self.data_directory, self.window_file), 'r')
        for i in range(self.Nmax):
            line = datafile.readline()
            while line.find('#') != -1:
                line = datafile.readline()
            self.W0[i] = float(line.split()[0])
            self.W2[i] = float(line.split()[1])
            self.W4[i] = float(line.split()[2])
        datafile.close()

        # Precompute useful window function things
        kmax = 100.
        self.k0 = 5.e-4

        self.rmin = 0.01
        rmax = 1000.
        b = -1.1001
        bR = -2.001

        Delta = log(kmax/self.k0) / (self.Nmax - 1)
        Delta_r = log(rmax/self.rmin) / (self.Nmax - 1)
        i_arr = np.arange(self.Nmax)
        rtab = self.rmin * exp(Delta_r * i_arr)

        self.kbins3 = self.k0 * exp(Delta * i_arr)
        self.tmp_factor = exp(-1.*b*i_arr*Delta)
        self.tmp_factor2 = exp(-1.*bR*i_arr*Delta_r)[:,np.newaxis]

        jsNm = np.arange(-self.Nmax//2,self.Nmax//2+1,1)
        self.etam = b + 2*1j*pi*(jsNm)/self.Nmax/Delta

        def J_func(r,nu):
            gam = special.gamma(2+nu)
            r_pow = r**(-3.-1.*nu)
            sin_nu = np.sin(pi*nu/2.)
            J0 = -1.*sin_nu*r_pow*gam/(2.*pi**2.)
            J2 = -1.*r_pow*(3.+nu)*gam*sin_nu/(nu*2.*pi**2.)
            return J0,J2

        j0,j2 = J_func(rtab.reshape(-1,1),self.etam.reshape(1,-1))
        self.J0_arr = j0[:,:,np.newaxis]
        self.J2_arr = j2[:,:,np.newaxis]

        self.etamR = bR + 2*1j*pi*(jsNm)/self.Nmax/Delta_r

        def Jk_func(k,nu):
            gam = special.gamma(2+nu)
            k_pow = k**(-3.-1.*nu)
            sin_nu = np.sin(pi*nu/2.)
            J0k = -1.*k_pow*gam*sin_nu*(4.*pi)
            J2k = -1.*k_pow*(3.+nu)*gam*sin_nu*4.*pi/nu
            return J0k,J2k

        j0k,j2k = Jk_func(self.kbins3.reshape(-1,1),self.etamR.reshape(1,-1))
        self.J0k_arr = j0k[:,:,np.newaxis]
        self.J2k_arr = j2k[:,:,np.newaxis]

    def loglkl(self, cosmo, data):
        """Compute the log-likelihood of the model, given the data and covariance"""

        ## First load in cosmological and explicitly-sampled nuisance parameters at this MCMC step
        h = cosmo.h()

        norm = 1.
        i_s=repr(3)
        a2 = 0.
        Nmax = self.Nmax
        k0 = self.k0

        z = self.z
        fz = cosmo.scale_independent_growth_factor_f(z)

        # Now load in (mean, sigma) for nuisance parameters that are analytically marginalized over
        Pshotsig = 5e3
        Pshotmean = 0.
        Nmarg = 4 # number of parameters to marginalize over analytically

        theory0vec = np.zeros((Nmax,Nmarg+1))
        theory2vec = np.zeros((Nmax,Nmarg+1))

        ## COMPUTE SPECTRA
        # Run CLASS-PT to get all components
        all_theory = cosmo.get_pk_mult(self.kbins3*h,self.z, Nmax)

        # Compute usual theory model
        kinloop1 = self.kbins3 * h

        # Generate the full theory model evaluated at the nuisance parameter means : this has all the EFT parameters removed.
        theory2 = norm**2.*all_theory[18] +norm**4.*all_theory[24]
        theory0 = norm**2.*all_theory[15] +norm**4.*all_theory[21] + Pshotmean

        # Compute derivatives for nuisance parameters which enter the model linearly
        dtheory2_dPshot = np.zeros_like(self.kbins3)

        dtheory0_dPshot = np.ones_like(self.kbins3)

        # Put all into a vector for simplicity
        theory0vec = np.vstack([theory0,dtheory0_dPshot]).T
        theory2vec = np.vstack([theory2,dtheory2_dPshot]).T

        # Now do a Fourier-transform to include the window function
        i_arr = np.arange(Nmax)
        factor = (exp(-1.*(self.kbins3*h/2.)**4.)*self.tmp_factor)[:,np.newaxis]
        Pdiscrin0 = theory0vec*factor
        Pdiscrin2 = theory2vec*factor

        cm0 = np.fft.fft(Pdiscrin0,axis=0)/ Nmax
        cm2 = np.fft.fft(Pdiscrin2,axis=0)/ Nmax
        cmsym0 = np.zeros((Nmax+1,Nmarg+1),dtype=np.complex_)
        cmsym2 = np.zeros((Nmax+1,Nmarg+1),dtype=np.complex_)

        all_i = np.arange(Nmax+1)
        f = (all_i+2-Nmax//2) < 1
        k0t1 = (k0**(-self.etam[f]))[:,np.newaxis]
        k0t2 = (k0**(-self.etam[~f]))[:,np.newaxis]
        cmsym0[f] = k0t1*np.conjugate(cm0[-all_i[f]+Nmax//2])
        cmsym2[f] = k0t1*np.conjugate(cm2[-all_i[f]+Nmax//2])
        cmsym0[~f] = k0t2*cm0[all_i[~f]-Nmax//2]
        cmsym2[~f] = k0t2*cm2[all_i[~f]-Nmax//2]

        cmsym0[-1] = cmsym0[-1] / 2
        cmsym0[0] = cmsym0[0] / 2
        cmsym2[-1] = cmsym2[-1] / 2
        cmsym2[0] = cmsym2[0] / 2

        xi0 = np.real(cmsym0[np.newaxis,:,:]*self.J0_arr).sum(axis=1)
        xi2 = np.real(cmsym2[np.newaxis,:,:]*self.J2_arr).sum(axis=1)
        i_arr = np.arange(Nmax)
        Xidiscrin0 = (xi0*self.W0 + 0.2*xi2*self.W2)*self.tmp_factor2
        Xidiscrin2 = (xi0*self.W2 + xi2*(self.W0 + 2.*(self.W2+self.W4)/7.))*self.tmp_factor2

        cmr0 = np.fft.fft(Xidiscrin0,axis=0)/ Nmax
        cmr2 = np.fft.fft(Xidiscrin2,axis=0)/ Nmax

        cmsymr0 = np.zeros((Nmax+1,Nmarg+1),dtype=np.complex_)
        cmsymr2 = np.zeros((Nmax+1,Nmarg+1),dtype=np.complex_)

        arr_i = np.arange(Nmax+1)
        f = (arr_i+2-Nmax//2)<1
        r0t1 = self.rmin**(-self.etamR[f])[:,np.newaxis]
        r0t2 = self.rmin**(-self.etamR[~f])[:,np.newaxis]
        cmsymr0[f] = r0t1*np.conjugate(cmr0[-arr_i[f] + Nmax//2])
        cmsymr2[f] = r0t1*np.conjugate(cmr2[-arr_i[f] + Nmax//2])
        cmsymr0[~f] = r0t2*cmr0[arr_i[~f] - Nmax//2]
        cmsymr2[~f] = r0t2*cmr2[arr_i[~f] - Nmax//2]

        cmsymr0[-1] = cmsymr0[-1] / 2
        cmsymr0[0] = cmsymr0[0] / 2
        cmsymr2[-1] = cmsymr2[-1] / 2
        cmsymr2[0] = cmsymr2[0] / 2

        P0t = np.real(cmsymr0[np.newaxis,:,:]*self.J0k_arr).sum(axis=1)
        P2t = np.real(cmsymr2[np.newaxis,:,:]*self.J2k_arr).sum(axis=1)

        P0int = np.asarray([interpolate.InterpolatedUnivariateSpline(self.kbins3,P0t[:,i])(self.k) for i in range(Nmarg+1)]).T
        P2int = np.asarray([interpolate.InterpolatedUnivariateSpline(self.kbins3,P2t[:,i])(self.k) for i in range(Nmarg+1)]).T

        # Compute the modified covariance matrix after including the nuisance-parameter marginalization
        dPshot_stack = np.hstack([P0int[:,4],P2int[:,4],0.,0.])

        marg_covMM = self.cov + Pshotsig**2*np.outer(dPshot_stack,dPshot_stack)
        invcov_margMM = np.linalg.inv(marg_covMM)

        # COMPUTE CHI^2 OF MODEL
        chi2 = 0.

        # Compute [model - data] for P(k) multipoles
        x1 = np.hstack([P0int[:,0]-self.Pk0,P2int[:,0]-self.Pk2,alphapar,alphaperp])

        # Compute chi2
        chi2 = np.inner(x1,np.inner(invcov_margMM,x1));

        # Correct for the new covariance matrix determinant
        chi2 += np.linalg.slogdet(marg_covMM)[1] - np.linalg.slogdet(self.cov)[1]

        # Compute log-likelihood
        loglkl = -0.5 * chi2
        return loglkl
