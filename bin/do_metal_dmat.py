#!/usr/bin/env python

import scipy as sp
import fitsio
import argparse
import glob
import healpy
import sys
from functools import partial

from scipy import random 
from scipy.interpolate import interp1d

from pylya import constants
from pylya import cf
from pylya.data import delta

from multiprocessing import Pool,Process,Lock,Manager,cpu_count,Value

def calc_metal_dmat(abs_igm1,abs_igm2,p):
    tmp = cf.metal_dmat(p,abs_igm1=abs_igm1,abs_igm2=abs_igm2)
    return tmp

if __name__ == '__main__':

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--out', type = str, default = None, required=True,
                        help = 'output file name')

    parser.add_argument('--in-dir', type = str, default = None, required=True,
                        help = 'data directory')

    parser.add_argument('--rp-max', type = float, default = 200, required=False,
                        help = 'max rp')

    parser.add_argument('--rt-max', type = float, default = 200, required=False,
                        help = 'max rt')

    parser.add_argument('--np', type = int, default = 50, required=False,
                        help = 'number of r-parallel bins')

    parser.add_argument('--nt', type = int, default = 50, required=False,
                        help = 'number of r-transverse bins')

    parser.add_argument('--lambda-abs', type = float, default = constants.lya, required=False,
                        help = 'wavelength of absorption')

    parser.add_argument('--fid-Om', type = float, default = 0.315, required=False,
                    help = 'Om of fiducial cosmology')

    parser.add_argument('--nside', type = int, default = 8, required=False,
                    help = 'healpix nside')

    parser.add_argument('--nproc', type = int, default = None, required=False,
                    help = 'number of processors')

    parser.add_argument('--z-ref', type = float, default = 2.25, required=False,
                    help = 'reference redshift')

    parser.add_argument('--rej', type = float, default = 1., required=False,
                    help = 'reference redshift')

    parser.add_argument('--z-evol', type = float, default = 2.9, required=False,
                    help = 'exponent of the redshift evolution of the delta field')

    parser.add_argument('--metal-alpha', type = float, default = 1., required=False,
                    help = 'exponent of the redshift evolution of the metal delta field')

    parser.add_argument('--nspec', type=int,default=None, required=False,
                    help = 'maximum spectra to read')

    parser.add_argument('--abs-igm', type=str,default=None, required=False,nargs="*",
                    help = 'list of metals')

    args = parser.parse_args()

    if args.nproc is None:
        args.nproc = cpu_count()/2

    print "nproc",args.nproc

    cf.rp_max = args.rp_max
    cf.rt_max = args.rt_max
    cf.np = args.np
    cf.nt = args.nt

    ## use a metal grid equal to the lya grid
    cf.npm = args.np
    cf.ntm = args.nt

    cf.nside = args.nside
    cf.zref = args.z_ref
    cf.alpha = args.z_evol
    cf.lambda_abs = args.lambda_abs
    cf.rej = args.rej

    cosmo = constants.cosmo(args.fid_Om)
    cf.cosmo=cosmo



    z_min_pix = 1.e6
    fi = glob.glob(args.in_dir+"/*.fits.gz")
    data = {}
    ndata = 0
    for i,f in enumerate(fi):
        sys.stderr.write("\rread {} of {} {}".format(i,len(fi),ndata))
        hdus = fitsio.FITS(f)
        dels = [delta.from_fitsio(h) for h in hdus[1:]]
        ndata+=len(dels)
        phi = [d.ra for d in dels]
        th = [sp.pi/2-d.dec for d in dels]
        pix = healpy.ang2pix(cf.nside,th,phi)
        for d,p in zip(dels,pix):
            if not p in data:
                data[p]=[]
            data[p].append(d)

            z = 10**d.ll/args.lambda_abs-1
            z_min_pix = sp.amin( sp.append([z_min_pix],z) )
            d.r_comov = cosmo.r_comoving(z)
            d.we *= ((1+z)/(1+args.z_ref))**(cf.alpha-1)
        if not args.nspec is None:
            if ndata>args.nspec:break
    sys.stderr.write("\n")

    cf.angmax = 2.*sp.arcsin(cf.rt_max/(2.*cosmo.r_comoving(z_min_pix)))

    cf.npix = len(data)
    cf.data = data
    cf.ndata = ndata
    cf.alpha_met = args.metal_alpha
    print "done"

    cf.counter = Value('i',0)

    cf.lock = Lock()
    
    cpu_data = {}
    for i,p in enumerate(data.keys()):
        ip = i%args.nproc
        if not ip in cpu_data:
            cpu_data[ip] = []
        cpu_data[ip].append(p)

    random.seed(0)

    for i,p in enumerate(cpu_data.values()):
        print "filling neighs ",i,len(cpu_data.values())
        cf.fill_neighs(p)

    dm_all=[]
    wdm_all=[]
    rp_all=[]
    rt_all=[]
    z_all=[]
    names=[]
    npairs_all=[]
    npairs_used_all=[]
    abs_igm = ["LYA"]+args.abs_igm
    for i,abs_igm1 in enumerate(abs_igm):
        for j in range(i,len(abs_igm)):
            if i==0 and j==0:
                continue
            abs_igm2 = abs_igm[j]
            cf.counter.value=0
            f=partial(calc_metal_dmat,abs_igm1,abs_igm2)
            sys.stderr.write("\n")
            pool = Pool(processes=args.nproc)
            dm = pool.map(f,cpu_data.values())
            pool.close()
            dm = sp.array(dm)
            wdm =dm[:,0].sum(axis=0)
            rp = dm[:,2].sum(axis=0)
            rt = dm[:,3].sum(axis=0)
            z = dm[:,4].sum(axis=0)
            we = dm[:,5].sum(axis=0)
            w=we>0
            rp[w]/=we[w]
            rt[w]/=we[w]
            z[w]/=we[w]
            npairs=dm[:,6].sum(axis=0)
            npairs_used=dm[:,7].sum(axis=0)
            dm=dm[:,1].sum(axis=0)
            w=wdm>0
            dm[w,:]/=wdm[w,None]

            dm_all.append(dm)
            wdm_all.append(wdm)
            rp_all.append(rp)
            rt_all.append(rt)
            z_all.append(z)
            names.append(abs_igm1+"_"+abs_igm2)

            npairs_all.append(npairs)
            npairs_used_all.append(npairs_used)

    out = fitsio.FITS(args.out,'rw',clobber=True)
    head = {}
    head["ALPHA_MET"]=cf.alpha_met
    head['REJ']=args.rej
    head['RPMAX']=cf.rp_max
    head['RTMAX']=cf.rt_max
    head['NT']=cf.nt
    head['NP']=cf.np

    out.write([sp.array(npairs_all),sp.array(npairs_used_all),sp.array(names)],names=["NPALL","NPUSED","ABS_IGM"],header=head)

    out_list = []
    out_names=[]
    for i,ai in enumerate(names):
        out_names=out_names + ["RP_"+ai]
        out_list = out_list + [rp_all[i]]

        out_names=out_names + ["RT_"+ai]
        out_list = out_list + [rt_all[i]]

        out_names=out_names + ["Z_"+ai]
        out_list = out_list + [z_all[i]]

        out_names = out_names + ["DM_"+ai]
        out_list = out_list + [dm_all[i]]

        out_names=out_names+["WDM_"+ai]
        out_list = out_list+[wdm_all[i]]

    out.write(out_list,names=out_names)
    out.close()

    