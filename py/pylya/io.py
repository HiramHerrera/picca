import fitsio
import scipy as sp
import healpy
import glob
import sys
import time 

from pylya.data import forest

def read_dlas(fdla):
    f=open(fdla)
    dlas={}
    for l in f:
        l = l.split()
        if len(l)==0:continue
        if l[0][0]=="#":continue
        if l[0]=="ThingID":continue
        if l[0][0]=="-":continue
        thid = int(l[0])
        if not dlas.has_key(thid):
            dlas[int(l[0])]=[]
        zabs = float(l[9])
        nhi = float(l[10])
        dlas[thid].append((zabs,nhi))

    return dlas

def read_drq(drq,zmin,zmax,keep_bal):
    vac = fitsio.FITS(drq)
    try:
        zqso = vac[1]["Z"][:] 
    except:
        sys.stderr.write("Z not found (new DRQ >= DRQ14 style), using Z_VI (DRQ <= DRQ12)\n")
        zqso = vac[1]["Z_VI"][:] 
    thid = vac[1]["THING_ID"][:]
    ra = vac[1]["RA"][:]
    dec = vac[1]["DEC"][:]
    try:
        bal_flag = vac[1]["BAL_FLAG_VI"][:]
    except:
        sys.stderr.write("BAL_FLAG_VI not found, ignoring BAL\n")
        bal_flag = sp.zeros(len(dec))

    ## info of the primary observation
    plate = vac[1]["PLATE"][:]
    mjd = vac[1]["MJD"][:]
    fid = vac[1]["FIBERID"][:]
    ## sanity
    w = thid>0
    w = w &  (zqso > zmin) & (zqso < zmax)
    if not keep_bal:
        w = w & (bal_flag == 0)

    ra = ra[w] * sp.pi / 180
    dec = dec[w] * sp.pi / 180
    zqso = zqso[w]
    thid = thid[w]
    plate = plate[w]
    mjd = mjd[w]
    fid = fid[w]
    vac.close()
    return ra,dec,zqso,thid,plate,mjd,fid

target_mobj = 500
nside_min = 8
def read_data(in_dir,drq,mode,zmin = 2.1,zmax = 3.5,nspec=None,log=None,keep_bal=False):
    ra,dec,zqso,thid,plate,mjd,fid = read_drq(drq,zmin,zmax,keep_bal)

    if mode == "pix":
        ## hardcoded for now, need to coordinate with Jose how to get this info
        nside = 8
        pixs = healpy.ang2pix(nside, sp.pi / 2 - dec, ra)
    elif mode == "spec" or mode =="corrected-spec":
        nside = 256
        pixs = healpy.ang2pix(nside, sp.pi / 2 - dec, ra)
        mobj = sp.bincount(pixs).sum()/len(sp.unique(pixs))

        ## determine nside such that there are 1000 objs per pixel on average
        sys.stderr.write("determining nside\n")
        while mobj<target_mobj and nside >= nside_min:
            nside /= 2
            pixs = healpy.ang2pix(nside, sp.pi / 2 - dec, ra)
            mobj = sp.bincount(pixs).sum()/len(sp.unique(pixs))
        sys.stderr.write("nside = {} -- mean #obj per pixel = {}\n".format(nside,mobj))
        if log is not None:
            log.write("nside = {} -- mean #obj per pixel = {}\n".format(nside,mobj))

    data ={}
    ndata = 0

    ## minimum number of unmasked forest pixels after rebinning

    upix = sp.unique(pixs)
    for i, pix in enumerate(upix):
        w = pixs == pix
        ## read all hiz qsos
        if mode == "pix":
            t0 = time.time()
            pix_data = read_from_pix(in_dir,pix,thid[w], ra[w], dec[w], zqso[w], plate[w], mjd[w], fid[w],log=log)
            read_time=time.time()-t0
        elif mode == "spec" or mode =="corrected-spec":
            t0 = time.time()
            pix_data = read_from_spec(in_dir,thid[w], ra[w], dec[w], zqso[w], plate[w], mjd[w], fid[w],mode=mode,log=log)
            read_time=time.time()-t0
        if not pix_data is None:
            sys.stderr.write("{} read from pix {}, {} {} in {} secs per spectrum\n".format(len(pix_data),pix,i,len(upix),read_time/(len(pix_data)+1e-3)))
        if not pix_data is None and len(pix_data)>0:
            data[pix] = pix_data
            ndata += len(pix_data)

        if not nspec is None:
            if ndata > nspec:break
	
    return data,ndata

def read_from_spec(in_dir,thid,ra,dec,zqso,plate,mjd,fid,mode,log=None):
    pix_data = []
    for t,r,d,z,p,m,f in zip(thid,ra,dec,zqso,plate,mjd,fid):
        try:
            fid = str(f)
            if f<10:
                fid='0'+fid
            if f<100:
                fid = '0'+fid
            if f<1000:
                fid = '0'+fid
            fin = in_dir + "/{}/{}-{}-{}-{}.fits".format(p,mode,p,m,fid)
            h = fitsio.FITS(fin)
        except IOError:
            log.write("error reading {}\n".format(fin))
            continue

        log.write("{} read\n".format(fin))
        ll = h[1]["loglam"][:]
        fl = h[1]["flux"][:]
        iv = h[1]["ivar"][:]*(h[1]["and_mask"]==0)
        d = forest(ll,fl,iv, t, r, d, z, p, m, f)
        pix_data.append(d)
        h.close()
    return pix_data

def read_from_pix(in_dir,pix,thid,ra,dec,zqso,plate,mjd,fid,log=None):
        try:
            fin = in_dir + "/pix_{}.fits.gz".format(pix)
	    h = fitsio.FITS(fin)
        except IOError:
            try:
                fin = in_dir + "/pix_{}.fits".format(pix)
                h = fitsio.FITS(fin)
            except IOError:
                print "error reading {}".format(pix)
                return None

        ## fill log
        if log is not None:
            for t in thid:
                if t not in h[0][:]:
                    log.write("{} missing from pixel {}\n".format(t,pix))
                    sys.stderr.write("{} missing from pixel {}\n".format(t,pix))

        pix_data=[]
        thid_list=list(h[0][:])
        thid2idx = {t:thid_list.index(t) for t in thid if t in thid_list}
        loglam  = h[1][:]
        flux = h[2].read()
        ivar = h[3].read()
        andmask = h[4].read()
        for (t, r, d, z, p, m, f) in zip(thid, ra, dec, zqso, plate, mjd, fid):
            idx = thid2idx[t]
            d = forest(loglam,flux[:,idx],ivar[:,idx]*(andmask[:,idx]==0), t, r, d, z, p, m, f)

            log.write("{} read\n".format(t))
            pix_data.append(d)
        h.close()
        return pix_data
