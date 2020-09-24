"""This module defines a set of functions to manage reading of data.

This module provides a class (Metadata) and several functions:
    - read_dlas
    - read_absorbers
    - read_drq
    - read_dust_map
    - read_data
    - read_from_spec
    - read_from_mock_1d
    - read_from_pix
    - read_from_spcframe
    - read_from_spplate
    - read_from_desi
    - read_deltas
    - read_objects
See the respective documentation for details
"""
import glob
import sys
import time
import os.path
import copy
import numpy as np
import healpy
import fitsio
from astropy.table import Table

from picca.utils import userprint
from picca.data import Forest, Delta, QSO
from picca.prep_pk1d import exp_diff, spectral_resolution
from picca.prep_pk1d import spectral_resolution_desi


## use a metadata class to simplify things
class Metadata(object):
    """Class defined to organize the storage of metadata.

    Attributes:
        thingid: integer or None
            Thingid of the observation.
        ra: float or None
            Right-ascension of the quasar (in radians).
        dec: float or None
            Declination of the quasar (in radians).
        z_qso: float or None
            Redshift of the quasar.
        plate: integer or None
            Plate number of the observation.
        mjd: integer or None
            Modified Julian Date of the observation.
        fiberid: integer or None
            Fiberid of the observation.
        order: 0 or 1 or None
            Order of the log10(lambda) polynomial for the continuum fit

    Methods:
        __init__
    """

    def __init__(self):
        """Initialize instance."""
        self.thingid = None
        self.ra = None
        self.dec = None
        self.z_qso = None
        self.plate = None
        self.mjd = None
        self.fiberid = None
        self.order = None


def read_dlas(filename):
    """Reads the DLA catalog from a fits file.

    ASCII or DESI files can be converted using:
        utils.eBOSS_convert_DLA()
        utils.desi_convert_DLA()

    Args:
        filename: str
            File containing the DLAs

    Returns:
        A dictionary with the DLA's information. Keys are the THING_ID
        associated with the DLA. Values are a tuple with its redshift and
        column density.
    """
    columns_list = ['THING_ID', 'Z', 'NHI']
    hdul = fitsio.FITS(filename)
    cat = {col: hdul['DLACAT'][col][:] for col in columns_list}
    hdul.close()

    # sort the items in the dictionary according to THING_ID and redshift
    w = np.argsort(cat['Z'])
    for key in cat.keys():
        cat[key] = cat[key][w]
    w = np.argsort(cat['THING_ID'])
    for key in cat.keys():
        cat[key] = cat[key][w]

    # group DLAs on the same line of sight together
    dlas = {}
    for thingid in np.unique(cat['THING_ID']):
        w = (thingid == cat['THING_ID'])
        dlas[thingid] = list(zip(cat['Z'][w], cat['NHI'][w]))
    num_dlas = np.sum([len(dla) for dla in dlas.values()])

    userprint('\n')
    userprint(' In catalog: {} DLAs'.format(num_dlas))
    userprint(' In catalog: {} forests have a DLA'.format(len(dlas)))
    userprint('\n')

    return dlas


def read_absorbers(filename):
    """Reads the absorbers catalog from an ascii file.

    Args:
        filename: str
            File containing the absorbers

    Returns:
        A dictionary with the absorbers's information. Keys are the THING_ID
        associated with the DLA. Values are a tuple with its redshift and
        column density.
    """
    file = open(filename)
    absorbers = {}
    num_absorbers = 0
    col_names = None
    for line in file.readlines():
        cols = line.split()
        if len(cols) == 0:
            continue
        if cols[0][0] == "#":
            continue
        if cols[0] == "ThingID":
            col_names = cols
            continue
        if cols[0][0] == "-":
            continue
        thingid = int(cols[col_names.index("ThingID")])
        if thingid not in absorbers:
            absorbers[thingid] = []
        lambda_abs = float(cols[col_names.index("lambda")])
        absorbers[thingid].append(lambda_abs)
        num_absorbers += 1
    file.close()

    userprint("")
    userprint(" In catalog: {} absorbers".format(num_absorbers))
    userprint(" In catalog: {} forests have absorbers".format(len(absorbers)))
    userprint("")

    return absorbers


def read_drq(drq_filename, z_min=0, z_max=10., keep_bal=False, bi_max=None, mode='sdss'):
    """Reads the quasars in the DRQ quasar catalog.

    Args:
        drq_filename: str
            Filename of the DRQ catalogue
        z_min: float - default: 0.
            Minimum redshift. Quasars with redshifts lower than z_min will be
            discarded
        z_max: float - default: 10.
            Maximum redshift. Quasars with redshifts higher than or equal to
            z_max will be discarded
        keep_bal: bool - default: False
            If False, remove the quasars flagged as having a Broad Absorption
            Line. Ignored if bi_max is not None
        bi_max: float or None - default: None
            Maximum value allowed for the Balnicity Index to keep the quasar

    Returns:
        The arrays containing
            ra: the right ascension of the quasars (in radians)
            dec: the declination of the quasars (in radians)
            z_qso: the redshift of the quasars
            thingid: the thingid of the observations
            plate: the plates of the observations
            mjd: the Modified Julian Date of the observation
            fiberid: the fiberid of the observations
    """
    userprint('Reading catalog from ', drq_filename)
    catalog = Table(fitsio.read(drq_filename, ext=1))

    keep_columns = ['RA', 'DEC', 'Z']
    if 'desi' in mode:
        obj_id_name='TARGETID'
        catalog.rename_column('TARGET_RA', 'RA')
        catalog.rename_column('TARGET_DEC', 'DEC')
        keep_columns += ['TARGETID', 'TILEID', 'PETAL_LOC', 'NIGHT', 'FIBER']
    else: 
        obj_id_name='THING_ID'
        keep_columns += ['THING_ID', 'PLATE', 'MJD', 'FIBERID']
    

    ## Redshift
    if 'Z' not in catalog.colnames:
        if 'Z_VI' in catalog.colnames:
            catalog.rename_column('Z_VI', 'Z')
            userprint("Z not found (new DRQ >= DRQ14 style), using Z_VI (DRQ <= DRQ12)")
        else:
            userprint("ERROR: No valid column for redshift found in ", drq_filename)
            return None

    ## Sanity checks
    userprint('')
    w = np.ones(len(catalog), dtype=bool)
    userprint(f" start                 : nb object in cat = {np.sum(w)}")
    w &= catalog[obj_id_name] > 0
    userprint(f" and thingid > 0       : nb object in cat = {np.sum(w)}")
    w &= catalog['RA'] != catalog['DEC']
    userprint(f" and ra != dec         : nb object in cat = {np.sum(w)}")
    w &= catalog['RA'] != 0.
    userprint(f" and ra != 0.          : nb object in cat = {np.sum(w)}")
    w &= catalog['DEC'] != 0.
    userprint(f" and dec != 0.         : nb object in cat = {np.sum(w)}")

    ## Redshift range
    w &= catalog['Z'] >= z_min
    userprint(f" and z >= {z_min}        : nb object in cat = {np.sum(w)}")
    w &= catalog['Z'] < z_max
    userprint(f" and z < {z_max}         : nb object in cat = {np.sum(w)}")

    ## BAL visual
    if keep_bal == False and bi_max is None:
        if 'BAL_FLAG_VI' in catalog.colnames:
            bal_flag = catalog['BAL_FLAG_VI']
            w &= bal_flag == 0
            userprint(f" and BAL_FLAG_VI == 0  : nb object in cat = {np.sum(w)}")
            keep_columns += ['BAL_FLAG_VI']
        else:
            userprint("WARNING: BAL_FLAG_VI not found")


    ## BAL CIV
    if bi_max is not None:
        if 'BI_CIV' in catalog.colnames:
            bi = catalog['BI_CIV']
            w &= bi <= bi_max
            userprint(f" and BI_CIV <= bi_max  : nb object in cat = {np.sum(w)}")
            keep_columns += ['BI_CIV']
        else:
            userprint("ERROR: --bi-max set but no BI_CIV field in HDU")
            sys.exit(0)

    #-- DLA Column density
    if 'NHI' in catalog.colnames:
        keep_columns += ['NHI']

    catalog.keep_columns(keep_columns)
    w = np.where(w)[0]
    catalog = catalog[w]

    #-- Convert angles to radians
    catalog['RA'] = np.radians(catalog['RA'])
    catalog['DEC'] = np.radians(catalog['DEC'])

    return catalog

def read_dust_map(drq_filename, extinction_conversion_r=3.793):
    """Reads the dust map.

    Args:
        drq_filename: str
            Filename of the DRQ catalogue
        extinction_conversion_r: float
            Conversion from E(B-V) to total extinction for band r.
            Note that the EXTINCTION values given in DRQ are in fact E(B-V)

    Returns:
        A dictionary with the extinction map. Keys are the THING_ID
        associated with the observation. Values are the extinction for that
        line of sight.
    """
    hdul = fitsio.read(drq_filename, ext=1)
    thingid = hdul['THING_ID']
    ext = hdul['EXTINCTION'][:, 1] / extinction_conversion_r
    return dict(zip(thingid, ext))

def read_data(in_dir,
              drq_filename,
              mode,
              z_min=2.1,
              z_max=3.5,
              max_num_spec=None,
              log_file=None,
              keep_bal=False,
              bi_max=None,
              best_obs=False,
              single_exp=False,
              pk1d=None,
              spall=None):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        in_dir: str
            Directory to spectra files. If mode is "spec-mock-1D", then it is
            the filename of the fits file contianing the mock spectra
        drq_filename: str
            Filename of the DRQ catalogue
        mode: str
            One of 'pix', 'spec', 'spcframe', 'spplate', 'corrected-spec',
            'spec-mock-1d' or 'desi'. Open mode of the spectra files
        z_min: float - default: 2.1
            Minimum redshift. Quasars with redshifts lower than z_min will be
            discarded
        z_max: float - default: 3.5
            Maximum redshift. Quasars with redshifts higher than or equal to
            z_max will be discarded
        max_num_spec: int or None - default: None
            Maximum number of spectra to read
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log
        keep_bal: bool - default: False
            If False, remove the quasars flagged as having a Broad Absorption
            Line. Ignored if bi_max is not None
        bi_max: float or None - default: None
            Maximum value allowed for the Balnicity Index to keep the quasar
        best_obs: bool - default: False
            If set, reads only the best observation for objects with repeated
            observations
        single_exp: bool - default: False
            If set, reads only one observation for objects with repeated
            observations (chosen randomly)
        pk1d: str or None - default: None
            Format for Pk 1D: Pk1D
        spall: str - default: None
            Path to the spAll file required for multiple observations

    Returns:
        The following variables:
            data: A dictionary with the data. Keys are the healpix numbers of
                each spectrum. Values are lists of Forest instances.
            num_data: Number of spectra in data.
            nside: The healpix nside parameter.
            "RING": The healpix pixel ordering used.
    """
    userprint("mode: " + mode)
    # read quasar characteristics from DRQ or DESI-miniSV catalogue

    catalog = read_drq(drq_filename,
                        z_min=z_min,
                        z_max=z_max,
                        keep_bal=keep_bal,
                        bi_max=bi_max,
                        mode=mode)

    # if there is a maximum number of spectra, make sure they are selected
    # in a contiguous regions
    if max_num_spec is not None:
        ## choose them in a small number of pixels
        healpixs = healpy.ang2pix(16, np.pi / 2 - catalog['DEC'], catalog['RA'])
        sorted_healpix = np.argsort(healpixs)
        catalog = catalog[sorted_healpix][:max_num_spec]

    data = {}
    num_data = 0

    # read data taking the mode into account
    if mode == "desi":
        nside = 8
        userprint("Found {} qsos".format(len(z_qso)))
        data, num_data = read_from_desi(nside,
                                        in_dir,
                                        catalog,
                                        pk1d=pk1d)

    elif mode in ["spcframe", "spplate", "spec", "corrected-spec"]:
        nside, healpixs = find_nside(catalog['RA'].data, catalog['DEC'].data, log_file)

        if mode == "spcframe":
            pix_data = read_from_spcframe(in_dir,
                                          catalog,
                                          log_file=log_file,
                                          single_exp=single_exp)
        elif mode == "spplate":
            pix_data = read_from_spplate(in_dir,
                                         catalog,
                                         log_file=log_file,
                                         best_obs=best_obs,
                                         spall=spall)
        else:
            pix_data = read_from_spec(in_dir,
                                      catalog,
                                      mode=mode,
                                      log_file=log_file,
                                      pk1d=pk1d,
                                      best_obs=best_obs,
                                      spall=spall)
        ra = np.array([d.ra for d in pix_data])
        dec = np.array([d.dec for d in pix_data])
        healpixs = healpy.ang2pix(nside, np.pi / 2 - dec, ra)
        for index, healpix in enumerate(healpixs):
            if healpix not in data:
                data[healpix] = []
            data[healpix].append(pix_data[index])
            num_data += 1

    elif mode in ["pix", "spec-mock-1D"]:
        data = {}
        num_data = 0

        if mode == "pix":
            try:
                filename = in_dir + "/master.fits.gz"
                hdul = fitsio.FITS(filename)
            except IOError:
                try:
                    filename = in_dir + "/master.fits"
                    hdul = fitsio.FITS(filename)
                except IOError:
                    try:
                        filename = in_dir + "/../master.fits"
                        hdul = fitsio.FITS(filename)
                    except IOError:
                        userprint("error reading master")
                        sys.exit(1)
            nside = hdul[1].read_header()['NSIDE']
            hdul.close()
            healpixs = healpy.ang2pix(nside, np.pi / 2 - catalog['DEC'], catalog['RA'])
        else:
            nside, healpixs = find_nside(catalog['RA'], catalog['DEC'], log_file)

        unique_healpix = np.unique(healpixs)

        for index, healpix in enumerate(unique_healpix):
            w = healpixs == healpix
            ## read all hiz qsos
            if mode == "pix":
                t0 = time.time()
                pix_data = read_from_pix(in_dir,
                                         healpix,
                                         catalog[w],
                                         log_file=log_file)
                read_time = time.time() - t0
            elif mode == "spec-mock-1D":
                t0 = time.time()
                pix_data = read_from_mock_1d(in_dir,
                                             catalog[w],
                                             log_file=log_file)
                read_time = time.time() - t0

            if not pix_data is None:
                userprint(
                    ("{} read from pix {}, {} {} in {} secs per"
                     "spectrum").format(len(pix_data), healpix, index,
                                        len(unique_healpix),
                                        read_time / (len(pix_data) + 1e-3)))
            if not pix_data is None and len(pix_data) > 0:
                data[healpix] = pix_data
                num_data += len(pix_data)

    elif mode=="desiminisv":
        nside = 8
        #userprint("Found {} qsos".format(len(z_qso)))
        data, num_data = read_from_minisv_desi(in_dir,
                                        catalog,
                                        pk1d=pk1d)
    else:
        userprint("I don't know mode: {}".format(mode))
        sys.exit(1)

    return data, num_data, nside, "RING"


def find_nside(ra, dec, log_file):
    """Determines nside such that there are 1000 objs per pixel on average.

    Args:
        ra: array of floats
            The right ascension of the quasars (in radians)
        dec: array of floats
            The declination of the quasars (in radians)
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log

    Returns:
        The value of nside and the healpixs for the objects
    """
    ## determine nside such that there are 1000 objs per pixel on average
    userprint("determining nside")
    nside = 256
    healpixs = healpy.ang2pix(nside, np.pi / 2 - dec, ra)
    mean_num_obj = len(healpixs) / len(np.unique(healpixs))
    target_mean_num_obj = 500
    nside_min = 8
    while mean_num_obj < target_mean_num_obj and nside >= nside_min:
        nside //= 2
        healpixs = healpy.ang2pix(nside, np.pi / 2 - dec, ra)
        mean_num_obj = len(healpixs) / len(np.unique(healpixs))
    userprint("nside = {} -- mean #obj per pixel = {}".format(
        nside, mean_num_obj))
    if log_file is not None:
        log_file.write(("nside = {} -- mean #obj per pixel"
                        " = {}\n").format(nside, mean_num_obj))

    return nside, healpixs


def read_from_spec(in_dir,
                   catalog,
                   mode,
                   log_file=None,
                   pk1d=None,
                   best_obs=False,
                   spall=None):
    """Reads the spectra from the individual SDSS spectrum format,
       spec-PLATE-MJD-FIBERID.fits,
       and formats its data as Forest instances.

    Args:
        in_dir: str
            Directory to spectra files
        catalog: astropy.table.Table
            Table containing catalog with objects 
        mode: str
            One of 'spec' or 'corrected-spec'. Open mode of the spectra files
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log
        pk1d: str or None - default: None
            Format for Pk 1D: Pk1D
        best_obs: bool - default: False
            If set, reads only the best observation for objects with repeated
            observations
        spall: str - default: None
            Path to the spAll file required for multiple observations

    Returns:
        List of read spectra for all the healpixs
    """

    ## if using multiple observations,
    ## then obtain all plate, mjd, fiberid 
    ## by what's available in spAll
    if not best_obs:
        thing_id_all, plate_all, mjd_all, fiberid_all = read_spall(in_dir, catalog['THING_ID'], spall=spall)
    
    userprint(f"Reading {len(catalog)} objects")

    pix_data = []
    #-- Loop over unique objects
    for i in range(len(catalog)):
        thing_id = catalog['THING_ID'][i]
        
        if not best_obs:
            w = thing_id_all == thing_id
            plates = plate_all[w]*1
            mjds = mjd_all[w]*1
            fibers = fiberid_all[w]*1
        else:
            metadata = catalog[i]
            plates = [metadata['PLATE']]
            mjds = [metadata['MJD']]
            fibers = [metadata['FIBERID']]

        deltas = None
        #-- Loop over all plate, mjd, fiberid for this object
        for plate, mjd, fiberid in zip(plates, mjds, fibers):
            filename = f'{in_dir}/{plate}/{mode}-{plate}-{mjd}-{fiberid:04d}.fits'
            try:
                hdul = fitsio.FITS(filename)
            except IOError:
                userprint("Error reading {}".format(filename))
                continue
            userprint("Read {}".format(filename))

            log_lambda = hdul[1]["loglam"][:]
            flux = hdul[1]["flux"][:]
            ivar = hdul[1]["ivar"][:] * (hdul[1]["and_mask"][:] == 0)

            #-- Define dispersion and resolution for pk1d
            if pk1d is not None:
                #-- Compute difference between exposure
                exposures_diff = exp_diff(hdul, log_lambda)
                #-- Compute spectral resolution
                wdisp = hdul[1]["wdisp"][:]
                reso = spectral_resolution(wdisp, True, fiberid, log_lambda)
            else:
                exposures_diff = None
                reso = None

            forest = Forest(log_lambda,
                            flux,
                            ivar,
                            thing_id,
                            catalog['RA'][i],
                            catalog['DEC'][i],
                            catalog['Z'][i],
                            plate,
                            mjd,
                            fiberid,
                            exposures_diff=exposures_diff,
                            reso=reso)
            if deltas is None:
                deltas = forest
            else:
                deltas.coadd(forest)
            hdul.close()

        if deltas is not None:
            pix_data.append(deltas)

    return pix_data


def read_from_mock_1d(filename,
                      catalog,
                      log_file=None):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        filename: str
            Filename of the fits file contianing the mock spectra
        catalog: astropy.table
            Table with object catalog
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log

    Returns:
        List of read spectra for all the healpixs
    """
    pix_data = []

    try:
        hdul = fitsio.FITS(filename)
    except IOError:
        log_file.write("error reading {}\n".format(filename))

    for entry in catalog:
        thing_id = entry['THING_ID']
        hdu = hdul[f'{thing_id}']
        log_file.write(f"file: {filename} hdus {hdu} read  \n")
        wave = hdu["wavelength"][:]
        log_lambda = np.log10(wave)
        flux = hdu["flux"][:]
        error = hdu["error"][:]
        ivar = 1.0 / error**2

        # compute difference between exposure
        exposures_diff = np.zeros(len(wave))
        # compute spectral resolution
        wdisp = hdu["psf"][:]
        reso = spectral_resolution(wdisp)

        # compute the mean expected flux
        mean_flux_transmission = hdu.read_header()["MEANFLUX"]
        cont = hdu["continuum"][:]
        mef = mean_flux_transmission * cont
        pix_data.append(
            Forest(log_lambda, flux, ivar, 
                   entry['THING_ID'], 
                   entry['RA'], entry['DEC'], entry['Z'], 
                   entry['PLATE'], entry['MJD'], entry['FIBERID'],
                   exposures_diff, reso, mef))

    hdul.close()

    return pix_data

#-- Is anyone using this? 
def read_from_pix(in_dir,
                  healpix,
                  catalog,
                  log_file=None):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        in_dir: str
            Directory to spectra files
        healpix: int
            The pixel number of a particular healpix
        thingid: array of int
            Thingid of the observations
        ra: array of float
            Right-ascension of the quasars (in radians)
        dec: array of float
            Declination of the quasars (in radians)
        z_qso: array of float
            Redshift of the quasars
        plate: array of integer
            Plate number of the observations
        mjd: array of integer
            Modified Julian Date of the observations
        fiberid: array of integer
            Fiberid of the observations
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log
        pk1d: str or None - default: None
            Format for Pk 1D: Pk1D
        best_obs: bool - default: False
            If set, reads only the best observation for objects with repeated
            observations

    Returns:
        List of read spectra for all the healpixs
    """
    try:
        filename = in_dir + "/pix_{}.fits.gz".format(healpix)
        hdul = fitsio.FITS(filename)
    except IOError:
        try:
            filename = in_dir + "/pix_{}.fits".format(healpix)
            hdul = fitsio.FITS(filename)
        except IOError:
            userprint("error reading {}".format(healpix))
            return None

    ## fill log
    if log_file is not None:
        for t in catalog['THING_ID']:
            if t not in hdul[0][:]:
                log_file.write("{} missing from pixel {}\n".format(t, healpix))
                userprint("{} missing from pixel {}".format(t, healpix))

    pix_data = []
    thingid_list = list(hdul[0][:])
    thingid2index = {
        t: thingid_list.index(t) for t in catalog['THING_ID'] if t in thingid_list
    }
    log_lambda = hdul[1][:]
    flux = hdul[2].read()
    ivar = hdul[3].read()
    mask = hdul[4].read()
    for entry in catalog:
        try:
            index = thingid2index[entry['THING_ID']]
        except KeyError:
            if log_file is not None:
                log_file.write("{} missing from pixel {}\n".format(t, healpix))
            userprint("{} missing from pixel {}".format(t, healpix))
            continue
        pix_data.append(
            Forest(log_lambda, flux[:, index],
                   ivar[:, index] * (mask[:, index] == 0), 
                   entry['THING_ID'], entry['RA'], entry['DEC'], entry['Z'], 
                   entry['PLATE'], entry['MJD'], entry['FIBERID']
                   )
            )
        if log_file is not None:
            log_file.write("{} read\n".format(t))
    hdul.close()

    return pix_data

#-- Not implemented yet
def read_from_spcframe(in_dir,
                       catalog,
                       log_file=None,
                       single_exp=False):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        in_dir: str
            Directory to spectra files
        thingid: array of int
            Thingid of the observations
        ra: array of float
            Right-ascension of the quasars (in radians)
        dec: array of float
            Declination of the quasars (in radians)
        z_qso: array of float
            Redshift of the quasars
        plate: array of integer
            Plate number of the observations
        mjd: array of integer
            Modified Julian Date of the observations
        fiberid: array of integer
            Fiberid of the observations
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log
        single_exp: bool - default: False
            If set, reads only one observation for objects with repeated
            observations (chosen randomly)

    Returns:
        List of read spectra for all the healpixs
    """
    if not single_exp:
        userprint(("ERROR: multiple observations not (yet) compatible with "
                   "spframe option"))
        userprint("ERROR: rerun with the --single-exp option")
        sys.exit(1)

    # store all the metadata in a single variable
    all_metadata = []
    for t, r, d, z, p, m, f in zip(thingid, ra, dec, z_qso, plate, mjd,
                                   fiberid):
        metadata = Metadata()
        metadata.thingid = t
        metadata.ra = r
        metadata.dec = d
        metadata.z_qso = z
        metadata.plate = p
        metadata.mjd = m
        metadata.fiberid = f
        all_metadata.append(metadata)

    # group the metadata with respect to their plate and mjd
    platemjd = {}
    for index in range(len(thingid)):
        if (plate[index], mjd[index]) not in platemjd:
            platemjd[(plate[index], mjd[index])] = []
        platemjd[(plate[index], mjd[index])].append(all_metadata[index])

    pix_data = {}
    userprint("reading {} plates".format(len(platemjd)))

    for key in platemjd:
        p, m = key
        # list all the exposures
        exps = []
        spplate = in_dir + "/{0}/spPlate-{0}-{1}.fits".format(p, m)
        userprint("INFO: reading file {}".format(spplate))
        hdul = fitsio.FITS(spplate)
        header = hdul[0].read_header()
        hdul.close()

        for card_suffix in ["B1", "B2", "R1", "R2"]:
            card = "NEXP_{}".format(card_suffix)
            if card in header:
                num_exp = header["NEXP_{}".format(card_suffix)]
            else:
                continue
            for index_exp in range(1, num_exp + 1):
                card = "EXPID{:02d}".format(index_exp)
                if not card in header:
                    continue
                exps.append(header[card][:11])

        userprint("INFO: found {} exposures in plate {}-{}".format(
            len(exps), p, m))

        if len(exps) == 0:
            continue

        # select a single exposure randomly
        selected_exps = [exp[3:] for exp in exps]
        selected_exps = np.unique(selected_exps)
        np.random.shuffle(selected_exps)
        selected_exps = selected_exps[0]

        for exp in exps:
            if single_exp:
                # if the exposure is not selected, ignore it
                if not selected_exps in exp:
                    continue
            t0 = time.time()
            # find the spectrograph number
            spectro = int(exp[1])
            assert spectro in [1, 2]

            spcframe = fitsio.FITS(in_dir +
                                   "/{}/spCFrame-{}.fits".format(p, exp))

            flux = spcframe[0].read()
            ivar = spcframe[1].read() * (spcframe[2].read() == 0)
            log_lambda = spcframe[3].read()

            ## now convert all those fluxes into forest objects
            for metadata in platemjd[key]:
                if spectro == 1 and metadata.fiberid > 500:
                    continue
                if spectro == 2 and metadata.fiberid <= 500:
                    continue
                index = (metadata.fiberid - 1) % 500
                t = metadata.thingid
                r = metadata.ra
                d = metadata.dec
                z = metadata.z_qso
                f = metadata.fiberid
                if t in pix_data:
                    pix_data[t].coadd(Forest(log_lambda[index], flux[index],
                                          ivar[index], t, r, d, z, p, m, f))
                else:
                    pix_data[t] = Forest(log_lambda[index], flux[index],
                                         ivar[index], t, r, d, z, p, m, f)
                if log_file is not None:
                    log_file.write(("{} read from exp {} and"
                                    " mjd {}\n").format(t, exp, m))
            num_read = len(platemjd[key])

            userprint(
                ("INFO: read {} from {} in {} per spec. Progress: "
                 "{} of {} \n").format(num_read, exp,
                                       (time.time() - t0) / (num_read + 1e-3),
                                       len(pix_data), len(thingid)))
            spcframe.close()

    data = list(pix_data.values())

    return data


def read_from_spplate(in_dir,
                      catalog,
                      log_file=None,
                      best_obs=False,
                      spall=None):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        in_dir: str
            Directory to spectra files
        catalog: astropy.table
            Table containing metadata of objects 
        log_file: _io.TextIOWrapper or None - default: None
            Opened file to print log
        best_obs: bool - default: False
            If set, reads only the best observation for objects with repeated
            observations
        spall: str - default: None
            Path to the spAll file required for multiple observations

    Returns:
        List of read spectra for all the healpixs
    """

    ## if using multiple observations,
    ## then replace thingid, plate, mjd, fiberid
    ## by what's available in spAll
    if not best_obs:
        thing_id_all, plate_all, mjd_all, fiberid_all = read_spall(in_dir, catalog['THING_ID'], spall=spall)
    else:
        thing_id_all = catalog['THING_ID']
        plate_all = catalog['PLATE']
        mjd_all = catalog['MJD']
        fiberid_all = catalog['FIBERID']
    
    #-- Helper to find information on catalog
    index = np.argsort(catalog['THING_ID'].data)
    sorted_index = np.searchsorted(catalog['THING_ID'][index], thing_id_all) 
    index_all_to_catalog = index[sorted_index]

    #-- We will sort all objects by plate-mjd 
    #-- since we want to open each file just once
    platemjd = {}
    for i in range(thing_id_all.size):
        entry = catalog[index_all_to_catalog[i]]
        p = plate_all[i]
        m = mjd_all[i]
        metadata = {k: entry[k] for k in entry.colnames}
        metadata['PLATE'] = p
        metadata['MJD'] = m
        metadata['FIBERID'] = fiberid_all[i]
        if (p, m) not in platemjd:
            platemjd[(p, m)] = []
        platemjd[(p, m)].append(metadata)

    userprint("reading {} plates".format(len(platemjd)))

    pix_data = {}
    for key in platemjd:
        p, m = key
        spplate = f'{in_dir}/{p}/spPlate-{p:04d}-{m}.fits'

        try:
            hdul = fitsio.FITS(spplate)
            header = hdul[0].read_header()
        except IOError:
            log_file.write("error reading {}\n".format(spplate))
            continue

        t0 = time.time()

        coeff0 = header["COEFF0"]
        coeff1 = header["COEFF1"]

        flux = hdul[0].read()
        ivar = hdul[1].read() * (hdul[2].read() == 0)
        log_lambda = coeff0 + coeff1 * np.arange(flux.shape[1])

        #-- Loop over all objects inside this spPlate file
        #-- and create the Forest objects
        for metadata in platemjd[(p, m)]:
            t = metadata['THING_ID']
            i = metadata['FIBERID'] - 1
            forest = Forest(log_lambda, flux[i], ivar[i], 
                            metadata['THING_ID'], 
                            metadata['RA'], metadata['DEC'], metadata['Z'],
                            metadata['PLATE'], metadata['MJD'], metadata['FIBERID'])
            if t in pix_data:
                pix_data[t].coadd(forest)
            else:
                pix_data[t] = forest
            if log_file is not None:
                log_file.write(f"{t} read from file {spplate} and mjd {m}\n")

        num_read = len(platemjd[(p, m)])
        time_read = (time.time() - t0) / (num_read + 1e-3)
        userprint(f"INFO: read {num_read} from {os.path.basename(spplate)}"+
                 f" in {time_read:.3f} per spec. "+
                 f" Progress: {len(pix_data)}"+
                 f" of {len(catalog)} ")
        hdul.close()

    data = list(pix_data.values())
    return data

#-- Not implemented yet
def read_from_desi(nside,
                   in_dir,
                   catalog,
                   pk1d=None):
    """Reads the spectra and formats its data as Forest instances.

    Args:
        nside: int
            The healpix nside parameter
        in_dir: str
            Directory to spectra files
        catalog: astropy.table
            Table containing metadata of objects 
        pk1d: str or None - default: None
            Format for Pk 1D: Pk1D

    Returns:
        List of read spectra for all the healpixs
    """
    in_nside = int(in_dir.split('spectra-')[-1].replace('/', ''))
    nest = True
    data = {}
    num_data = 0

    z_table = dict(zip(thingid, z_qso))
    in_healpixs = healpy.ang2pix(in_nside, np.pi / 2. - dec, ra, nest=nest)
    unique_in_healpixs = np.unique(in_healpixs)

    for index, healpix in enumerate(unique_in_healpixs):
        filename = (in_dir + "/" + str(int(healpix / 100)) + "/" +
                    str(healpix) + "/spectra-" + str(in_nside) + "-" +
                    str(healpix) + ".fits")

        userprint(("\rread {} of {}. "
                   "num_data: {}").format(index, len(unique_in_healpixs),
                                          num_data))
        try:
            hdul = fitsio.FITS(filename)
        except IOError:
            userprint("Error reading pix {}\n".format(healpix))
            continue

        ## get the quasars
        thingid_qsos = thingid[(in_healpixs == healpix)]
        plate_qsos = plate[(in_healpixs == healpix)]
        mjd_qsos = mjd[(in_healpixs == healpix)]
        fiberid_qsos = fiberid[(in_healpixs == healpix)]
        if 'TARGET_RA' in hdul["FIBERMAP"].get_colnames():
            ra = hdul["FIBERMAP"]["TARGET_RA"][:] * np.pi / 180.
            dec = hdul["FIBERMAP"]["TARGET_DEC"][:] * np.pi / 180.
        elif 'RA_TARGET' in hdul["FIBERMAP"].get_colnames():
            ## TODO: These lines are for backward compatibility
            ## Should be removed at some point
            ra = hdul["FIBERMAP"]["RA_TARGET"][:] * np.pi / 180.
            dec = hdul["FIBERMAP"]["DEC_TARGET"][:] * np.pi / 180.
        healpixs = healpy.ang2pix(nside, np.pi / 2 - dec, ra)
        #exp = h["FIBERMAP"]["EXPID"][:]
        #night = h["FIBERMAP"]["NIGHT"][:]
        #fib = h["FIBERMAP"]["FIBER"][:]
        in_thingids = hdul["FIBERMAP"]["TARGETID"][:]

        spec_data = {}
        colors = ["B", "R"]
        if "Z_FLUX" in hdul:
             colors.append("Z")
        for color in  colors:
            spec = {}
            try:
                spec["log_lambda"] = np.log10(
                    hdul["{}_WAVELENGTH".format( color)].read())
                spec["FL"] = hdul["{}_FLUX".format( color)].read()
                spec["IV"] = (
                    hdul["{}_IVAR".format( color)].read() *
                    (hdul["{}_MASK".format(color)].read() == 0))
                w = np.isnan(spec["FL"]) | np.isnan(spec["IV"])
                for key in ["FL", "IV"]:
                    spec[key][w] = 0.
                if "{}_RESOLUTION".format(color) in hdul:
                    spec["RESO"] = hdul["{}_RESOLUTION".format(
                        color)].read()
                spec_data[color] = spec
            except OSError:
                userprint("error {}".format(color))
        hdul.close()

        for t, p, m, f in zip(thingid_qsos, plate_qsos, mjd_qsos, fiberid_qsos):
            w_t = in_thingids == t
            if w_t.sum() == 0:
                userprint("\nError reading thingid {}\n".format(t))
                continue

            forest = None
            for spec in spec_data.values():
                ivar = spec['IV'][w_t]
                flux = (ivar * spec['FL'][w_t]).sum(axis=0)
                ivar = ivar.sum(axis=0)
                w = ivar > 0.
                flux[w] /= ivar[w]
                if not pk1d is None:
                    reso_sum = spec['RESO'][w_t].sum(axis=0)
                    reso_in_km_per_s = spectral_resolution_desi(
                        reso_sum, spec['log_lambda'])
                    exposures_diff = np.zeros(spec['log_lambda'].shape)
                else:
                    reso_in_km_per_s = None
                    exposures_diff = None

                if forest is None:
                    forest = copy.deepcopy(
                        Forest(spec['log_lambda'], flux, ivar, t, ra[w_t][0],
                               dec[w_t][0], z_table[t], p, m, f,
                               exposures_diff, reso_in_km_per_s))
                else:
                    forest.coadd(
                        Forest(spec['log_lambda'], flux, ivar, t, ra[w_t][0],
                               dec[w_t][0], z_table[t], p, m, f,
                               exposures_diff, reso_in_km_per_s))

            pix = healpixs[w_t][0]
            if pix not in data:
                data[pix] = []
            data[pix].append(forest)
            num_data += 1

    userprint("found {} quasars in input files\n".format(num_data))
    if num_data==0:
        raise ValueError("No Quasars found, stopping here")
    return data, num_data

def read_from_minisv_desi(in_dir, catalog, pk1d=None):
    """Reads the spectra and formats its data as Forest instances.
    Unlike the read_from_desi routine, this orders things by tile/petal
    Routine used to treat the DESI mini-SV data.

    Args:
        in_dir: str
            Directory to spectra files
        catalog: astropy.table
            Table containing metadata of objects 
        pk1d: str or None - default: None
            Format for Pk 1D: Pk1D

    Returns:
        List of read spectra for all the healpixs
    """

    data = {}
    num_data = 0

    filenames = []
    for entry in catalog:
        fi = (f"{entry['TILEID']}/{entry['NIGHT']}/"+
              f"coadd-{entry['PETAL_LOC']}-{entry['TILEID']}-{entry['NIGHT']}.fits")
        filenames.append(fi)
    filenames = np.unique(filenames)

    for index, filename in enumerate(filenames):
        userprint("read tile {} of {}. ndata: {}".format(index,len(filenames),num_data))
        filename = in_dir+'/'+filename
        try:
            hdul = fitsio.FITS(filename)
        except IOError:
            userprint("Error reading file {}\n".format(filename))
            continue

        fibermap = hdul['FIBERMAP'].read()
        fibermap_colnames = hdul["FIBERMAP"].get_colnames()
        if 'TARGET_RA' in fibermap_colnames:
            ra = fibermap['TARGET_RA']
            dec = fibermap['TARGET_DEC']
        elif 'RA_TARGET' in fibermap_colnames:
            ra = fibermap['RA_TARGET']
            dec = fibermap['DEC_TARGET']
        ra = np.radians(ra)
        dec = np.radians(dec)

        petal_spec = fibermap['PETAL_LOC'][0]

        if 'TILEID' in fibermap_colnames:
            tile_spec = fibermap['TILEID'][0]
        else:
            #pre-andes tiles don't have this in the fibermap
            tile_spec=filename.split('-')[-2]

        if 'NIGHT' in fibermap_colnames:
            night_spec = fibermap['NIGHT'][0]
        else:
            #pre-andes tiles don't have this in the fibermap
            night_spec=int(filename.split('-')[-1].split('.')[0])

        targetid_spec = fibermap['TARGETID']

        if 'brz_wavelength' in hdul.hdu_map.keys():
            colors = ['BRZ']
            if index == 0:
                print("reading all-band coadd as in minisv pre-andes dataset")
        else:
            colors = ['B','R','Z']
            if index == 0:
                print("couldn't read the all band-coadd,"
                      " trying single band as introduced in Andes reduction")

        spec_data = {}
        for color in colors:
            try:
                spec={}
                spec['log_lambda'] = np.log10(hdul[f'{color}_WAVELENGTH'].read())
                spec['FL'] = hdul[f'{color}_FLUX'].read()
                spec['IV'] = (hdul[f'{color}_IVAR'].read() *
                             (hdul[f'{color}_MASK'].read()==0))
                w = np.isnan(spec['FL']) | np.isnan(spec['IV'])
                for key in ['FL','IV']:
                    spec[key][w] = 0.
                spec['RESO'] = hdul[f'{color}_RESOLUTION'].read()
                spec_data[color] = spec
            except OSError:
                userprint(f"ERROR: when reading {color}-band data")

        hdul.close()
        plate_spec = int(f"{tile_spec}{petal_spec}")

        select = ( (catalog['TILEID'] == tile_spec) &
                   (catalog['PETAL_LOC'] == petal_spec) & 
                   (catalog['NIGHT']  == night_spec) )
        userprint(f'This is tile {tile_spec}, petal {petal_spec}, night {night_spec}')

        #-- Loop over quasars in catalog inside this tile-petal
        for entry in catalog[select]:

            #-- Find which row in tile contains this quasar
            w_t = np.where(targetid_spec == entry['TARGETID'])[0][0]

            #-- Loop over three spectrograph arms and coadd fluxes
            forest = None
            for spec in spec_data.values():
                ivar = spec['IV'][w_t]*1
                flux = spec['FL'][w_t]*1

                if pk1d is not None:
                    reso_sum = spec['RESO'][w_t]*1
                    reso_in_km_per_s = np.real(spectral_resolution_desi(
                        reso_sum, spec['log_lambda']))
                    exposures_diff = np.zeros(spec['log_lambda'].shape)
                else:
                    reso_in_km_per_s = None
                    exposures_diff = None

                forest_temp = Forest(spec['log_lambda'], flux, ivar,
                    entry['TARGETID'],
                    entry['RA'], entry['DEC'], entry['Z'],
                    entry['TILEID'], entry['NIGHT'], entry['FIBER'],  
                    exposures_diff, reso_in_km_per_s)
                if forest is None:
                    forest = forest_temp
                else:
                    forest.coadd(forest_temp)

            if plate_spec not in data:
                data[plate_spec]=[]
            data[plate_spec].append(forest)
            num_data+=1
    userprint("found {} quasars in input files\n".format(num_data))

    if num_data==0:
        raise ValueError("No Quasars found, stopping here")
    
    return data, num_data


def read_deltas(in_dir,
                nside,
                lambda_abs,
                alpha,
                z_ref,
                cosmo,
                max_num_spec=None,
                no_project=False,
                from_image=None):
    """Reads deltas and computes their redshifts.

    Fills the fields delta.z and multiplies the weights by
        `(1+z)^(alpha-1)/(1+z_ref)^(alpha-1)`
    (equation 7 of du Mas des Bourboux et al. 2020)

    Args:
        in_dir: str
            Directory to spectra files. If mode is "spec-mock-1D", then it is
            the filename of the fits file contianing the mock spectra
        nside: int
            The healpix nside parameter
        lambda_abs: float
            Wavelength of the absorption (in Angstroms)
        alpha: float
            Redshift evolution coefficient (see equation 7 of du Mas des
            Bourboux et al. 2020)
        z_ref: float
            Redshift of reference
        cosmo: constants.Cosmo
            The fiducial cosmology
        max_num_spec: int or None - default: None
            Maximum number of spectra to read
        no_project: bool - default: False
            If True, project the deltas (see equation 5 of du Mas des Bourboux
            et al. 2020)
        from_image: list or None - default: None
            If not None, read the deltas from image files. The list of
            filenname for the image files should be paassed in from_image

    Returns:
        The following variables:
            data: A dictionary with the data. Keys are the healpix numbers of
                each spectrum. Values are lists of delta instances.
            num_data: Number of spectra in data.
            z_min: Minimum redshift of the loaded deltas.
            z_max: Maximum redshift of the loaded deltas.

    Raises:
        AssertionError: if no healpix numbers are found
    """
    files = []
    in_dir = os.path.expandvars(in_dir)
    if from_image is None or len(from_image) == 0:
        if len(in_dir) > 8 and in_dir[-8:] == '.fits.gz':
            files += glob.glob(in_dir)
        elif len(in_dir) > 5 and in_dir[-5:] == '.fits':
            files += glob.glob(in_dir)
        else:
            files += glob.glob(in_dir + '/*.fits') + glob.glob(in_dir +
                                                               '/*.fits.gz')
    else:
        for arg in from_image:
            if len(arg) > 8 and arg[-8:] == '.fits.gz':
                files += glob.glob(arg)
            elif len(arg) > 5 and arg[-5:] == '.fits':
                files += glob.glob(arg)
            else:
                files += glob.glob(arg + '/*.fits') + glob.glob(arg +
                                                                '/*.fits.gz')
    files = sorted(files)

    deltas = []
    num_data = 0
    for index, filename in enumerate(files):
        userprint("\rread {} of {} {}".format(index, len(files), num_data))
        if from_image is None:
            hdul = fitsio.FITS(filename)
            deltas += [Delta.from_fitsio(hdu) for hdu in hdul[1:]]
            hdul.close()
        else:
            deltas += Delta.from_image(filename)

        num_data = len(deltas)
        if max_num_spec is not None:
            if num_data > max_num_spec:
                break

    # truncate the deltas if we load too many lines of sight
    if max_num_spec is not None:
        deltas = deltas[:max_num_spec]
        num_data = len(deltas)

    userprint("\n")

    # compute healpix numbers
    phi = [delta.ra for delta in deltas]
    theta = [np.pi / 2. - delta.dec for delta in deltas]
    healpixs = healpy.ang2pix(nside, theta, phi)
    if healpixs.size == 0:
        raise AssertionError('ERROR: No data in {}'.format(in_dir))

    data = {}
    z_min = 10**deltas[0].log_lambda[0] / lambda_abs - 1.
    z_max = 0.
    for delta, healpix in zip(deltas, healpixs):
        z = 10**delta.log_lambda / lambda_abs - 1.
        z_min = min(z_min, z.min())
        z_max = max(z_max, z.max())
        delta.z = z
        if not cosmo is None:
            delta.r_comov = cosmo.get_r_comov(z)
            delta.dist_m = cosmo.get_dist_m(z)
        delta.weights *= ((1 + z) / (1 + z_ref))**(alpha - 1)

        if not no_project:
            delta.project()

        if not healpix in data:
            data[healpix] = []
        data[healpix].append(delta)

    return data, num_data, z_min, z_max


def read_objects(filename,
                 nside,
                 z_min,
                 z_max,
                 alpha,
                 z_ref,
                 cosmo,
                 keep_bal=True):
    """Reads objects and computes their redshifts.

    Fills the fields delta.z and multiplies the weights by
        `(1+z)^(alpha-1)/(1+z_ref)^(alpha-1)`
    (equation 7 of du Mas des Bourboux et al. 2020)

    Args:
        filename: str
            Filename of the objects catalogue (must follow DRQ catalogue
            structure)
        nside: int
            The healpix nside parameter
        z_min: float
            Minimum redshift. Quasars with redshifts lower than z_min will be
            discarded
        z_max: float
            Maximum redshift. Quasars with redshifts higher than or equal to
            z_max will be discarded
        alpha: float
            Redshift evolution coefficient (see equation 7 of du Mas des
            Bourboux et al. 2020)
        z_ref: float
            Redshift of reference
        cosmo: constants.Cosmo
            The fiducial cosmology
        keep_bal: bool
            If False, remove the quasars flagged as having a Broad Absorption
            Line. Ignored if bi_max is not None

    Returns:
        The following variables:
            objs: A list of QSO instances
            z_min: Minimum redshift of the loaded objects.

    Raises:
        AssertionError: if no healpix numbers are found
    """
    objs = {}

    catalog = read_drq(filename,
                       z_min=z_min,
                       z_max=z_max,
                       keep_bal=keep_bal)

    phi = catalog['RA']
    theta = np.pi / 2. - catalog['DEC']
    healpixs = healpy.ang2pix(nside, theta, phi)
    if healpixs.size == 0:
        raise AssertionError()
    userprint("Reading objects ")

    unique_healpix = np.unique(healpixs)
    for index, healpix in enumerate(unique_healpix):
        userprint("{} of {}".format(index, len(unique_healpix)))
        w = healpixs == healpix
        objs[healpix] = [
            QSO(entry['THING_ID'], 
                entry['RA'], entry['DEC'], entry['Z'], 
                entry['PLATE'], entry['MJD'], entry['FIBERID'])
            for entry in catalog[w]
        ]
        for obj in objs[healpix]:
            obj.weights = ((1. + obj.z_qso) / (1. + z_ref))**(alpha - 1.)
            if not cosmo is None:
                obj.r_comov = cosmo.get_r_comov(obj.z_qso)
                obj.dist_m = cosmo.get_dist_m(obj.z_qso)

    return objs, catalog['Z'].min()


def read_spall(in_dir, thingid, spall=None):
    """Loads thingid, plate, mjd, and fiberid from spAll file

    Args:
        in_dir: str
            Directory to spectra files
        thingid: array of int
            Thingid of the observations
        spall: str - default: None
            Path to the spAll file required for multiple observations
    Returns:
        Arrays with thingid, plate, mjd, and fiberid
    """
    if spall is None:
        folder = in_dir.replace("spectra/", "")
        folder = folder.replace("lite", "").replace("full", "")
        filenames = glob.glob(folder + "/spAll-*.fits")

        if len(filenames) > 1:
            userprint("ERROR: found multiple spAll files")
            userprint(("ERROR: try running with --bestobs option (but you will "
                       "lose reobservations)"))
            for filename in filenames:
                userprint("found: ", filename)
            sys.exit(1)
        if len(filenames) == 0:
            userprint(("ERROR: can't find required spAll file in "
                       "{}").format(in_dir))
            userprint(("ERROR: try runnint with --best-obs option (but you "
                       "will lose reobservations)"))
            sys.exit(1)
        spall = filenames[0]

    userprint(f"INFO: reading spAll from {spall}")
    spall = fitsio.read(spall,
                        columns=[
                            'THING_ID', 'PLATE', 'MJD', 'FIBERID',
                            'PLATEQUALITY', 'ZWARNING'
                        ])
    thingid_spall = spall["THING_ID"]
    plate_spall = spall["PLATE"]
    mjd_spall = spall["MJD"]
    fiberid_spall = spall["FIBERID"]
    quality_spall = spall["PLATEQUALITY"].astype(str)
    z_warn_spall = spall["ZWARNING"]

    w = np.in1d(thingid_spall, thingid)
    userprint(f"INFO: Found {np.sum(w)} spectra with required THING_ID" )
    w &= quality_spall == "good"
    userprint(f"INFO: Found {np.sum(w)} spectra with 'good' plate" )
    ## Removing spectra with the following ZWARNING bits set:
    ## SKY, LITTLE_COVERAGE, UNPLUGGED, BAD_TARGET, NODATA
    ## https://www.sdss.org/dr14/algorithms/bitmasks/#ZWARNING
    bad_z_warn_bit = {
        0: 'SKY',
        1: 'LITTLE_COVERAGE',
        7: 'UNPLUGGED',
        8: 'BAD_TARGET',
        9: 'NODATA'
    }
    for z_warn_bit, z_warn_bit_name in bad_z_warn_bit.items():
        wbit = (z_warn_spall & 2**z_warn_bit == 0)
        w &= wbit
        userprint(f"INFO: Found {np.sum(w)} spectra without {z_warn_bit} bit set: {z_warn_bit_name}")
    userprint(f"INFO: # unique objs: {len(thingid)}")
    userprint(f"INFO: # spectra: {w.sum()}")

    return thingid_spall[w], plate_spall[w], mjd_spall[w], fiberid_spall[w]
