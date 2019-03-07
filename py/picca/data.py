from __future__ import print_function

import scipy as sp
from picca import constants
from picca.utils import print, unred
import iminuit
from .dla import dla
import fitsio

def variance(var,eta,var_lss,fudge):
    return eta*var + var_lss + fudge/var


class qso:
    def __init__(self,thid,ra,dec,zqso,plate,mjd,fiberid):
        self.ra = ra
        self.dec = dec

        self.plate=plate
        self.mjd=mjd
        self.fid=fiberid

        ## cartesian coordinates
        self.xcart = sp.cos(ra)*sp.cos(dec)
        self.ycart = sp.sin(ra)*sp.cos(dec)
        self.zcart = sp.sin(dec)
        self.cosdec = sp.cos(dec)

        self.zqso = zqso
        self.thid = thid

    def __xor__(self,data):
        try:
            x = sp.array([d.xcart for d in data])
            y = sp.array([d.ycart for d in data])
            z = sp.array([d.zcart for d in data])
            ra = sp.array([d.ra for d in data])
            dec = sp.array([d.dec for d in data])

            cos = x*self.xcart+y*self.ycart+z*self.zcart
            w = cos>=1.
            if w.sum()!=0:
                print('WARNING: {} pairs have cos>=1.'.format(w.sum()))
                cos[w] = 1.
            w = cos<=-1.
            if w.sum()!=0:
                print('WARNING: {} pairs have cos<=-1.'.format(w.sum()))
                cos[w] = -1.
            angl = sp.arccos(cos)

            w = (sp.absolute(ra-self.ra)<constants.small_angle_cut_off) & (sp.absolute(dec-self.dec)<constants.small_angle_cut_off)
            if w.sum()!=0:
                angl[w] = sp.sqrt( (dec[w]-self.dec)**2 + (self.cosdec*(ra[w]-self.ra))**2 )
        except:
            x = data.xcart
            y = data.ycart
            z = data.zcart
            ra = data.ra
            dec = data.dec

            cos = x*self.xcart+y*self.ycart+z*self.zcart
            if cos>=1.:
                print('WARNING: 1 pair has cosinus>=1.')
                cos = 1.
            elif cos<=-1.:
                print('WARNING: 1 pair has cosinus<=-1.')
                cos = -1.
            angl = sp.arccos(cos)
            if (sp.absolute(ra-self.ra)<constants.small_angle_cut_off) & (sp.absolute(dec-self.dec)<constants.small_angle_cut_off):
                angl = sp.sqrt( (dec-self.dec)**2 + (self.cosdec*(ra-self.ra))**2 )
        return angl

class forest(qso):

    lmin = None
    lmax = None
    lmin_rest = None
    lmax_rest = None
    rebin = None
    dll = None

    ### Correction function for multiplicative errors in pipeline flux calibration
    correc_flux = None
    ### Correction function for multiplicative errors in inverse pipeline variance calibration
    correc_ivar = None

    ### map of g-band extinction to thids for dust correction
    ebv_map = None

    ## absorber pixel mask limit
    absorber_mask = None

    ## minumum dla transmission
    dla_mask = None

    var_lss = None
    eta = None
    mean_cont = None

    ## quality variables
    mean_SNR = None
    mean_reso = None
    mean_z = None

    ## resolution matrix for desi forests
    reso_matrix = None
    mean_reso_matrix = None
    reso_pix = None
    linear_binning = None


    def __init__(self, ll, fl, iv, thid, ra, dec, zqso, plate, mjd, fid, order, diff=None, reso=None, mmef=None, reso_matrix=None, reso_pix = None):
        qso.__init__(self, thid, ra, dec, zqso, plate, mjd, fid)
        if not self.ebv_map is None:
            corr = unred(10**ll,self.ebv_map[thid])
            fl /= corr
            iv *= corr**2
        # cut to specified range
        if forest.linear_binning:
            ll=10**ll
        bins = sp.floor((ll - forest.lmin) / forest.dll + 0.5).astype(int)
        ll = forest.lmin + bins * forest.dll

        w = (ll >= forest.lmin)
        w = w & (ll < forest.lmax)
        if not forest.linear_binning:
            w = w & (ll - sp.log10(1. + self.zqso) > forest.lmin_rest)
            w = w & (ll - sp.log10(1. + self.zqso) < forest.lmax_rest)
        else:
            w = w & (ll / (1. + self.zqso) > forest.lmin_rest)
            w = w & (ll / (1. + self.zqso) < forest.lmax_rest)
        w = w & (iv > 0.)
        if w.sum() == 0:
            return
        bins = bins[w]
        ll = ll[w]
        fl = fl[w]
        iv = iv[w]
        # mmef is the mean expected flux fraction using the mock continuum
        if mmef is not None:
            mmef = mmef[w]
        if diff is not None:
            diff = diff[w]
        if reso is not None:
            reso = reso[w]
        if reso_matrix is not None:
            reso_matrix = reso_matrix[:, w]
        if reso_pix is not None:
            reso_pix = reso_pix[w]
        # rebin
        cll = forest.lmin + sp.arange(bins.max() + 1) * forest.dll
        cfl = sp.zeros(bins.max() + 1)
        civ = sp.zeros(bins.max() + 1)
        if mmef is not None:
            cmmef = sp.zeros(bins.max() + 1)
        if reso is not None:
            creso = sp.zeros(bins.max() + 1)
        if reso_pix is not None:
            creso_pix = sp.zeros(bins.max() + 1)
        ccfl = sp.bincount(bins, weights=iv * fl)
        cciv = sp.bincount(bins, weights=iv)
        if mmef is not None:
            ccmmef = sp.bincount(bins, weights=iv * mmef)
        if diff is not None:
            cdiff = sp.bincount(bins, weights=iv * diff)
        if reso is not None:
            ccreso = sp.bincount(bins, weights=iv * reso)
        if reso_pix is not None:
            ccreso_pix = sp.bincount(bins, weights=iv * reso_pix)
        if reso_matrix is not None:
            creso_matrix = sp.zeros((reso_matrix.shape[0], bins.max() + 1))
            for i, r in enumerate(reso_matrix):
                # need to think about this, does rebinning even make sense for the resolution matrix, probably not, but to be able to get the following lines right this would be needed. And this is probably the best way if it is sensible at all, it might be necessary to compute everything in lambda instead of log(lambda) in the end
                creso_matrix[i, :] = sp.bincount(bins, weights=iv * r)

        cfl[:len(ccfl)] += ccfl
        civ[:len(cciv)] += cciv
        if mmef is not None:
            cmmef[:len(ccmmef)] += ccmmef
        if reso is not None:
            creso[:len(creso)] += ccreso
        if reso_pix is not None:
            creso_pix[:len(creso_pix)] += ccreso_pix
        w = (civ > 0.)
        if w.sum() == 0:
            return
        ll = cll[w]
        fl = cfl[w] / civ[w]
        iv = civ[w]
        if mmef is not None:
            mmef = cmmef[w] / civ[w]
        if diff is not None:
            diff = cdiff[w] / civ[w]
        if reso is not None:
            reso = creso[w] / civ[w]
        if reso_pix is not None:
            reso_pix = creso_pix[w] / civ[w]
        if reso_matrix is not None:
            reso_matrix = creso_matrix[:, w] / civ[sp.newaxis, w]




        ## Flux calibration correction
        if not self.correc_flux is None:
            correction = self.correc_flux(ll)
            fl /= correction
            iv *= correction**2
        if not self.correc_ivar is None:
            correction = self.correc_ivar(ll)
            iv /= correction

        self.T_dla = None
        self.ll = ll
        self.fl = fl
        self.iv = iv
        self.mmef = mmef
        self.order = order
        #if diff is not None :
        self.diff = diff
        self.reso = reso
        self.reso_matrix = reso_matrix
        self.reso_pix = reso_pix

#        else :
#           self.diff = sp.zeros(len(ll))
#           self.reso = sp.ones(len(ll))

        # compute means
        if reso is not None :
            if reso_matrix is not None:
                nremove=reso_matrix.shape[0]//2
                self.mean_reso = sp.mean(reso[nremove:-nremove]) #* constants.speed_light * 1000. * forest.dll * sp.log(10.0) #as I gave it reso_pix instead of km/s
            else:
                self.mean_reso = sp.mean(reso) #for maintaining the previous behaviour for the moment
        if reso_matrix is not None:
            nremove=reso_matrix.shape[0]//2
            self.mean_reso_matrix = sp.mean(reso_matrix[:,nremove:-nremove],axis=1)   #this might be extended by properly filtering out pixels where boundary effects play a role (instead of just removing 4 pixels on each side). This will also return an empty array for short spectra (and the FFT of this will be nan)
        err = 1.0/sp.sqrt(iv)
        SNR = fl/err
        self.mean_SNR = sp.mean(SNR)
        lam_lya = constants.absorber_IGM["LYA"]
        if not forest.linear_binning:
            self.mean_z = sp.mean([10.**ll[-1], 10.**ll[0]])/lam_lya -1.0
        else:
            self.mean_z = sp.mean([ll[-1], ll[0]])/lam_lya -1.0


    def __add__(self,d):

        if not hasattr(self,'ll') or not hasattr(d,'ll'):
            return self

        dic = {}  # this should contain all quantities that are to be coadded with ivar weighting

        ll = sp.append(self.ll,d.ll)
        dic['fl'] = sp.append(self.fl, d.fl)
        iv = sp.append(self.iv,d.iv)

        if self.mmef is not None:
            dic['mmef'] = sp.append(self.mmef, d.mmef)
        if self.diff is not None:
            dic['diff'] = sp.append(self.diff, d.diff)
        if self.reso is not None:
            dic['reso'] = sp.append(self.reso, d.reso)

        if self.reso_matrix is not None:
            dic['reso_matrix'] = sp.append(self.reso_matrix, d.reso_matrix,axis=1)
        if self.reso_pix is not None:
            dic['reso_pix'] = sp.append(self.reso_pix, d.reso_pix)

        bins = sp.floor((ll-forest.lmin)/forest.dll+0.5).astype(int)
        cll = forest.lmin + sp.arange(bins.max()+1)*forest.dll


        civ = sp.zeros(bins.max()+1)
        cciv = sp.bincount(bins,weights=iv)
        civ[:len(cciv)] += cciv
        w = (civ>0.)
        self.ll = cll[w]
        self.iv = civ[w]

        for k, v in dic.items():
            if len(v.shape)==1:
                cnew = sp.zeros(bins.max() + 1)
                ccnew = sp.bincount(bins, weights=iv * v)
                cnew[:len(ccnew)] += ccnew
                setattr(self, k, cnew[w] / civ[w])
            else:
                cnew = sp.zeros(v.shape[0],bins.max() + 1)
                for ivsub,vsub in enumerate(v):
                    ccsubnew = sp.bincount(bins, weights=iv * vsub)
                    cnew[ivsub,:len(ccnew)] += ccsubnew
                setattr(self, k, cnew[:,w] / civ[w])

        # recompute means of quality variables
        if self.reso is not None:
            self.mean_reso = self.reso.mean()
        if self.reso_matrix is not None:
            self.mean_reso_matrix = self.reso_matrix.mean(axis=1)
        err = 1./sp.sqrt(self.iv)
        SNR = self.fl/err
        self.mean_SNR = SNR.mean()
        lam_lya = constants.absorber_IGM["LYA"]
        if not self.linear_binning:
            self.mean_z = (sp.power(10.,ll[len(ll)-1])+sp.power(10.,ll[0]))/2./lam_lya -1.0
        else:
            self.mean_z = (ll[-1]+ll[0])/2./lam_lya -1.0

        return self

    def mask(self,mask_obs,mask_RF):
        if not hasattr(self,'ll'):
            return

        w = sp.ones(self.ll.size).astype(bool)
        for l in mask_obs:
            w = w & ( (self.ll<l[0]) | (self.ll>l[1]) )
        for l in mask_RF:
            w = w & ( (self.ll-sp.log10(1.+self.zqso)<l[0]) | (self.ll-sp.log10(1.+self.zqso)>l[1]) )

        self.ll = self.ll[w]
        self.fl = self.fl[w]
        self.iv = self.iv[w]
        if self.mmef is not None:
            self.mmef = self.mmef[w]
        if self.diff is not None:
            self.diff = self.diff[w]
        if self.reso is not None:
            self.reso = self.reso[w]
        if self.reso_matrix is not None:
            self.reso_matrix = self.reso_matrix[:,w]
        if self.reso_pix is not None:
            self.reso_pix = self.reso_pix[w]

        if self.reso is not None :
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso = sp.mean(self.reso)#[nremove:-nremove]) #* constants.speed_light * 1000. * forest.dll * sp.log(10.0) #as I gave it reso_pix instead of km/s
            else:
                self.mean_reso = sp.mean(self.reso)
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso_matrix = sp.mean(self.reso_matrix,axis=1)#[:,nremove:-nremove],axis=1)   #this might be extended by properly filtering out pixels where boundary effects play a role (instead of just removing 4 pixels on each side). This will also return an empty array for short spectra (and the FFT of this will be nan)

    def add_dla(self,zabs,nhi,mask=None):
        if not hasattr(self,'ll'):
            return
        if self.T_dla is None:
            self.T_dla = sp.ones(len(self.ll))

        self.T_dla *= dla(self,zabs,nhi).t

        w = (self.T_dla>forest.dla_mask)
        if not mask is None:
            if not self.linear_binning:
                for l in mask:
                    w = w & ( (self.ll-sp.log10(1.+zabs)<l[0]) | (self.ll-sp.log10(1.+zabs)>l[1]) )
            else:
                for l in mask:
                    w = w & ( (self.ll/(1.+zabs)<l[0]) | (self.ll/(1.+zabs)>l[1]) )

        self.iv = self.iv[w]
        self.ll = self.ll[w]
        self.fl = self.fl[w]
        if self.mmef is not None:
            self.mmef = self.mmef[w]
        self.T_dla = self.T_dla[w]
        if self.diff is not None :
            self.diff = self.diff[w]
        if self.reso is not None:
            self.reso = self.reso[w]
        if self.reso_pix is not None:
            self.reso_pix = self.reso_pix[w]

        if self.reso_matrix is not None:
            self.reso_matrix = self.reso_matrix[:,w]
        if self.reso is not None :
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso = sp.mean(self.reso[nremove:-nremove]) #* constants.speed_light * 1000. * forest.dll * sp.log(10.0) #as I gave it reso_pix instead of km/s
            else:
                self.mean_reso = sp.mean(self.reso)
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso_matrix = sp.mean(self.reso_matrix[:,nremove:-nremove],axis=1)   #this might be extended by properly filtering out pixels where boundary effects play a role (instead of just removing 4 pixels on each side). This will also return an empty array for short spectra (and the FFT of this will be nan)

    def add_absorber(self,lambda_absorber):
        if not hasattr(self,'ll'):
            return

        w = sp.ones(self.ll.size, dtype=bool)
        if not self.linear_binning:
            w &= sp.fabs(1.e4*(self.ll-sp.log10(lambda_absorber)))>forest.absorber_mask
        else:
            w &= sp.fabs(1.e4*(sp.log10(self.ll)-sp.log10(lambda_absorber)))>forest.absorber_mask

        self.iv = self.iv[w]
        self.ll = self.ll[w]
        self.fl = self.fl[w]
        if self.diff is not None :
            self.diff = self.diff[w]
        if self.reso is not None:
            self.reso = self.reso[w]
        if self.reso_matrix is not None:
             self.reso_matrix = self.reso_matrix[:,w]
        if self.reso_pix is not None:
             self.reso_pix = self.reso_pix[w]

        if self.reso is not None :
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso = sp.mean(self.reso)#[nremove:-nremove]) #* constants.speed_light * 1000. * forest.dll * sp.log(10.0) #as I gave it reso_pix instead of km/s
            else:
                self.mean_reso = sp.mean(self.reso)
            if self.reso_matrix is not None:
                nremove=self.reso_matrix.shape[0]//2
                self.mean_reso_matrix = sp.mean(self.reso_matrix,axis=1)#[:,nremove:-nremove],axis=1)   #this might be extended by properly filtering out pixels where boundary effects play a role (instead of just removing 4 pixels on each side). This will also return an empty array for short spectra (and the FFT of this will be nan)


    def cont_fit(self):
        if not self.linear_binning:
            lmax = forest.lmax_rest+sp.log10(1+self.zqso)
            lmin = forest.lmin_rest+sp.log10(1+self.zqso)
            try:
                mc = forest.mean_cont(self.ll-sp.log10(1+self.zqso))
            except ValueError:
                raise Exception
        else:
            lmax = forest.lmax_rest*(1+self.zqso)
            lmin = forest.lmin_rest*(1+self.zqso)
            try:
                mc = forest.mean_cont(self.ll/(1+self.zqso))
            except ValueError:
                raise Exception

        if not self.T_dla is None:
            mc*=self.T_dla

        var_lss = forest.var_lss(self.ll)
        eta = forest.eta(self.ll)
        fudge = forest.fudge(self.ll)

        def model(p0,p1):
            line = p1*(self.ll-lmin)/(lmax-lmin)+p0
            return line*mc

        def chi2(p0,p1):
            m = model(p0,p1)
            var_pipe = 1./self.iv/m**2
            ## prep_del.variance is the variance of delta
            ## we want here the we = ivar(flux)

            var_tot = variance(var_pipe,eta,var_lss,fudge)
            we = 1/m**2/var_tot

            # force we=1 when use-constant-weight
            # TODO: make this condition clearer, maybe pass an option
            # use_constant_weights?
            if (eta==0).all() :
                we=sp.ones(len(we))
            v = (self.fl-m)**2*we
            return v.sum()-sp.log(we).sum()

        p0 = (self.fl*self.iv).sum()/self.iv.sum()
        p1 = 0

        mig = iminuit.Minuit(chi2,p0=p0,p1=p1,error_p0=p0/2.,error_p1=p0/2.,errordef=1.,print_level=0,fix_p1=(self.order==0))
        fmin,_ = mig.migrad()

        self.co=model(mig.values["p0"],mig.values["p1"])
        self.p0 = mig.values["p0"]
        self.p1 = mig.values["p1"]

        self.bad_cont = None
        if not fmin.is_valid:
            self.bad_cont = "minuit didn't converge"
        if sp.any(self.co <= 0):
            self.bad_cont = "negative continuum"


        ## if the continuum is negative, then set it to a very small number
        ## so that this forest is ignored
        if self.bad_cont is not None:
            self.co = self.co*0+1e-10
            self.p0 = 0.
            self.p1 = 0.


class delta(qso):

    def __init__(self,thid,ra,dec,zqso,plate,mjd,fid,ll,we,co,de,order,iv,diff,m_SNR,m_reso,m_z,dll,m_reso_matrix=None,reso=None,reso_matrix=None,reso_pix=None,linear_binning=False):

        qso.__init__(self,thid,ra,dec,zqso,plate,mjd,fid)
        self.ll = ll
        self.we = we
        self.co = co
        self.de = de
        self.order = order
        self.iv = iv
        self.diff = diff
        self.mean_SNR = m_SNR
        self.mean_reso = m_reso
        self.mean_z = m_z
        self.dll = dll
        self.mean_reso_matrix = m_reso_matrix
        self.reso = reso
        self.reso_matrix = reso_matrix
        self.reso_pix = reso_pix
        self.linear_binning = linear_binning


    @classmethod
    def from_forest(cls,f,st,var_lss,eta,fudge,mc=False):

        ll = f.ll
        mst = st(ll)
        var_lss = var_lss(ll)
        eta = eta(ll)
        fudge = fudge(ll)

        #if mc is True use the mock continuum to compute the mean expected flux fraction
        if mc : mef = f.mmef
        else : mef = f.co * mst
        de = f.fl/ mef -1.
        var = 1./f.iv/mef**2
        we = 1./variance(var,eta,var_lss,fudge)
        diff = f.diff
        if f.diff is not None:
            diff /= mef
        iv = f.iv/(eta+(eta==0))*(mef**2)


        return cls(f.thid,f.ra,f.dec,f.zqso,f.plate,f.mjd,f.fid,ll,we,f.co,de,f.order,
                   iv,diff,f.mean_SNR,f.mean_reso,f.mean_z,f.dll,m_reso_matrix=f.mean_reso_matrix,
                   reso=f.reso,reso_matrix=f.reso_matrix,reso_pix=f.reso_pix,linear_binning=f.linear_binning)


    @classmethod
    def from_fitsio(cls,h,Pk1D_type=False):


        head = h.read_header()

        de = h['DELTA'][:]
        ll = h['LOGLAM'][:]


        if  Pk1D_type :
            iv = h['IVAR'][:]
            diff = h['DIFF'][:]
            m_SNR = head['MEANSNR']
            m_reso = head['MEANRESO']
            m_z = head['MEANZ']
            dll =  head['DLL']
            try:
                reso=h['RESO'][:]
            except (KeyError, ValueError):
                reso=None
            try:
                reso_pix=h['RESO_PIX'][:]
            except (KeyError, ValueError):
                reso_pix=None

            we = None
            co = None
            try:
                resomat=h['RESOMAT'][:]
                nremove=resomat.shape[0]//2
                mean_resomat=sp.mean(resomat[nremove:-nremove,:],axis=0)
            except (KeyError, ValueError):
                resomat = None
                mean_resomat = None

            iv=iv.astype(float)   #to ensure the endianess is right for the fft
            diff=diff.astype(float)
            de=de.astype(float)
            ll=ll.astype(float)
            if reso is not None:
                reso=reso.astype(float)
            if resomat is not None:
                resomat=resomat.astype(float)
            if reso_pix is not None:
                reso_pix=reso_pix.astype(float)
        else :
            iv = None
            diff = None
            m_SNR = None
            m_reso = None
            dll = None
            m_z = None
            we = h['WEIGHT'][:]
            co = h['CONT'][:]
            mean_resomat = None
            reso = None
            resomat = None
            reso_pix = None


        thid = head['THING_ID']
        ra = head['RA']
        dec = head['DEC']
        zqso = head['Z']
        plate = head['PLATE']
        mjd = head['MJD']
        fid = head['FIBERID']

        try:
            order = head['ORDER']
        except KeyError:
            order = 1
        return cls(thid,ra,dec,zqso,plate,mjd,fid,ll,we,co,de,order,
                   iv,diff,m_SNR,m_reso,m_z,dll,m_reso_matrix=mean_resomat,reso=reso,reso_matrix=resomat,reso_pix=reso_pix)


    @classmethod
    def from_ascii(cls,line):

        a = line.split()
        plate = int(a[0])
        mjd = int(a[1])
        fid = int(a[2])
        ra = float(a[3])
        dec = float(a[4])
        zqso = float(a[5])
        m_z = float(a[6])
        m_SNR = float(a[7])
        m_reso = float(a[8])
        dll = float(a[9])

        nbpixel = int(a[10])
        de = sp.array(a[11:11+nbpixel]).astype(float)
        ll = sp.array(a[11+nbpixel:11+2*nbpixel]).astype(float)
        iv = sp.array(a[11+2*nbpixel:11+3*nbpixel]).astype(float)
        diff = sp.array(a[11+3*nbpixel:11+4*nbpixel]).astype(float)
        reso = sp.array(a[11+4*nbpixel:11+5*nbpixel]).astype(float)

        try: #this could be used to get the mean resolution matrix
            nresomat = int(a[11+5*nbpixel])
            mean_resomat = sp.array(a[12+5*nbpixel:12+5*nbpixel+nresomat]).astype(float) #note that only the mean is written for the ascii. Better use fits tables if using reso matrices
        except IndexError:
            mean_resomat=None
        thid = 0
        order = 0
        we = None
        co = None

        return cls(thid,ra,dec,zqso,plate,mjd,fid,ll,we,co,de,order,
                   iv,diff,m_SNR,m_reso,m_z,dll,m_reso_matrix=mean_resomat,reso=reso,reso_matrix=None)

    @staticmethod
    def from_image(f):
        h=fitsio.FITS(f)
        de = h[0].read()
        iv = h[1].read()
        ll = h[2].read()
        ra = h[3]["RA"][:]*sp.pi/180.
        dec = h[3]["DEC"][:]*sp.pi/180.
        z = h[3]["Z"][:]
        plate = h[3]["PLATE"][:]
        mjd = h[3]["MJD"][:]
        fid = h[3]["FIBER"]
        thid = h[3]["THING_ID"][:]

        nspec = h[0].read().shape[1]
        deltas=[]
        for i in range(nspec):
            if i%100==0:
                print("\rreading deltas {} of {}".format(i,nspec),end="")

            delt = de[:,i]
            ivar = iv[:,i]
            w = ivar>0
            delt = delt[w]
            ivar = ivar[w]
            lam = ll[w]

            order = 1
            diff = None
            m_SNR = None
            m_reso = None
            dll = None
            m_z = None

            deltas.append(delta(thid[i],ra[i],dec[i],z[i],plate[i],mjd[i],fid[i],lam,ivar,None,delt,order,iv,diff,m_SNR,m_reso,m_z,dll))

        h.close()
        return deltas


    def project(self):
        mde = sp.average(self.de,weights=self.we)
        res=0
        if (self.order==1) and self.de.shape[0] > 1:
            mll = sp.average(self.ll,weights=self.we)
            mld = sp.sum(self.we*self.de*(self.ll-mll))/sp.sum(self.we*(self.ll-mll)**2)
            res = mld * (self.ll-mll)
        elif self.order==1:
            res = self.de

        self.de -= mde + res
