#!/usr/bin/env python
# -*- coding: iso-8859-15 -*-

# Copyright (C) 2017-2018 Centre National d'Etudes Spatiales (CNES)

import argparse, json, os, datetime, subprocess, sys

import numpy as np
from osgeo import gdal


def shellquote(s):
    return "'%s'" % s.replace("'", "'\\''")

garbage = list()


def remove(target):
    try:
        os.remove(target)
    except OSError:
        pass


def garbage_cleanup():
    """
    Removes all the files listed in the global variable 'garbage'.
    """
    while garbage:
        remove(garbage.pop())


def read_tiles(tiles_file):
    tiles = []
    outdir = os.path.dirname(tiles_file)

    with open(tiles_file) as f:
        tiles = f.readlines()

    # Strip trailing \n
    tiles = list(map(str.strip,tiles))
    tiles = [os.path.join(outdir, t) for t in tiles]

    return tiles


def vrt_body_source(fname, band, src_x, src_y, src_w, src_h, dst_x, dst_y, dst_w, dst_h):
    """
    Generate a source section in vrt body.

    Args:
        fname: Relative path to the source image
        band: index of the band to use as source
        src_x, src_y, src_w, src_h: source window (cropped from source image)
        dst_x, dst_y, dst_w, dst_h: destination window (where crop will be pasted)
    """

    body = '\t\t<SimpleSource>\n'
    body += '\t\t\t<SourceFileName relativeToVRT=\'1\'>%s</SourceFileName>\n' % fname
    body += '\t\t\t<SourceBand>%i</SourceBand>\n' % band
    body += '\t\t\t<SrcRect xOff=\'%i\' yOff=\'%i\'' % (src_x, src_y)
    body += 'xSize=\'%i\' ySize=\'%i\'/>\n' % (src_w, src_h)
    body += '\t\t\t<DstRect xOff=\'%i\' yOff=\'%i\'' % (dst_x, dst_y)
    body += 'xSize=\'%i\' ySize=\'%i\'/>\n' % (dst_w, dst_h)
    body += '\t\t</SimpleSource>\n'

    return body


def vrt_header(w, h, dataType='Float32', band=1, color=False):
    """
        Generate vrt up header

        Args:
            w,h: size of the corresponding raster
        """
    header = '<VRTDataset rasterXSize=\"%i\" rasterYSize=\"%i\">\n' % (w, h)
    header += vrt_bandheader(dataType=dataType, band=band, color=color)

    return header


def vrt_bandheader(dataType='Float32', band=1, color=False):
    """
    Generate vrt header for a monoband image

    Args:
        w,h: size of the corresponding raster
        band: band id
        color: color gray activation
        dataType: Type of the raster (default is Float32)

    """
    header = '\t<VRTRasterBand dataType=\"%s\" band=\"%d\">\n' % (dataType, band)
    if color == False:
        header += '\t\t<ColorInterp>Gray</ColorInterp>\n'

    return header


def vrt_footer():
    """
    Generate vrt footer
    """
    footer = vrt_bandfooter()
    footer += '</VRTDataset>\n'

    return footer


def vrt_bandfooter():
    """
    Generate vrt footer
    """
    footer = '\t</VRTRasterBand>\n'

    return footer


def global_extent(tiles):
    """
    Compute the global raster extent from a list of tiles
    Args:
        tiles: list of config files loaded from json files
    Returns:
         (min_x,max_x,min_y,max_y) tuple
    """
    min_x = None
    max_x = None
    min_y = None
    max_y = None

    # First loop is to compute global extent
    for tile in tiles:
        with open(tile, 'r') as f:

            tile_cfg = json.load(f)

            x = tile_cfg['roi']['x']
            y = tile_cfg['roi']['y']
            w = tile_cfg['roi']['w']
            h = tile_cfg['roi']['h']

            if min_x is None or x < min_x:
                min_x = x
            if min_y is None or y < min_y:
                min_y = y
            if max_x is None or x + w > max_x:
                max_x = x + w
            if max_y is None or y + h > max_y:
                max_y = y + h

    return (min_x, max_x, min_y, max_y)


def write_row_vrts(outDir, tiles, sub_img, vrt_basename, min_x, max_x, nbBands=1, dataType="Float32", color=False):
    """
    Write intermediate vrts (one per row)

    Args:
        outDir : output directory in which to store the vrts
        tiles: list of config files loaded from json files
        sub_img: Relative path of the sub-image to mosaic (for ex. height_map.tif)
        vrt_basename: basename of the output vrt
        min_x, max_x: col extent of the raster
    Returns:
        A dictionnary of vrt files with sections vrt_body, th and vrt_dir
    """
    vrt_row = {}

    # First loop, write all row vrts body section
    for tile in tiles:
        with open(tile, 'r') as f:

            tile_cfg = json.load(f)

            x = tile_cfg['roi']['x']
            y = tile_cfg['roi']['y']
            w = tile_cfg['roi']['w']
            h = tile_cfg['roi']['h']

            tile_dir = os.path.dirname(tile)

            vrt_row.setdefault(y, {'vrt_body': "", 'vrt_dir': outDir,
                                   'vrt_name': '{}_row{}'.format(vrt_basename, y), "th": h})

            tile_sub_img = os.path.join(tile_dir, sub_img)

            # Check if source image exists
            if not os.path.exists(tile_sub_img):
                # print('Warning: ' + tile_sub_img + ' does not exist, skipping ...')
                continue

            relative_sub_img_dir = os.path.relpath(os.path.abspath(tile_sub_img), vrt_row[y]['vrt_dir'])
            vrt_row[y]['vrt_body'] += vrt_body_source(relative_sub_img_dir,
                                                      1, 0, 0, w, h,
                                                      x - min_x, 0, w, h)

    # Second loop, write all row vrts
    # Do not use items()/iteritems() here because of python 2 and 3 compat
    for y in vrt_row:
        vrt_data = vrt_row[y]
        row_vrt_filename = os.path.join(vrt_data['vrt_dir'], vrt_data['vrt_name'])

        with open(row_vrt_filename, 'w') as row_vrt_file:
            # Write vrt header
            row_vrt_file.write(vrt_header(max_x - min_x, vrt_data['th'], dataType=dataType, color=color))

            for band in range(1, nbBands + 1):
                if band > 1:
                    row_vrt_file.write(vrt_bandheader(band=band, dataType=dataType, color=color))

                # Write vrt body
                if band > 1 :
                    row_vrt_file.write(vrt_data['vrt_body'].replace('<SourceBand>{}</SourceBand>'.format(1),
                                                                    '<SourceBand>{}</SourceBand>'.format(band)))
                else:
                    row_vrt_file.write(vrt_data['vrt_body'])

                if band != nbBands:
                    row_vrt_file.write(vrt_bandfooter())

            # Write vrt footer
            row_vrt_file.write(vrt_footer())

        garbage.append(row_vrt_filename)

    return vrt_row


def write_main_vrt(vrt_row, vrt_name, min_x, max_x, min_y, max_y, nbBands=1, dataType="Float32", color=False):
    """
    Write the main vrt file

    Args:
        vrt_row: The vrt files dictionnary from write_row_vrts()
        vrt_name: The output vrt_name
        min_x,max_x,min_y,max_y: Extent of the raster
    """
    vrt_basename = os.path.basename(vrt_name)
    vrt_dirname = os.path.dirname(vrt_name) or '.'

    with open(vrt_name, 'w') as main_vrt_file:
        main_vrt_file.write(vrt_header(max_x - min_x, max_y - min_y, dataType=dataType, color=color))

        for band in range(1,nbBands+1):
            if band > 1:
                main_vrt_file.write(vrt_bandheader(band=band, dataType=dataType, color=color))
            # Do not use items()/iteritems() here because of python 2 and 3 compat
            for y in vrt_row:
                vrt_data = vrt_row[y]
                relative_vrt_dir = os.path.relpath(vrt_data['vrt_dir'], vrt_dirname)
                row_vrt_filename = os.path.join(relative_vrt_dir, vrt_data['vrt_name'])

                vrt_body_src = vrt_body_source(row_vrt_filename, band, 0, 0, max_x - min_x,
                                               vrt_data['th'], 0, y - min_y, max_x - min_x,
                                               vrt_data['th'])

                main_vrt_file.write(vrt_body_src)
            if band != nbBands:
                main_vrt_file.write(vrt_bandfooter())
        main_vrt_file.write(vrt_footer())


def write_vrt_from_a_list(vrt_outfile, tiles_list):
    """
    Write a vrt file from a list of tiles as tuples

    Args:
    tiles_tiles: list of tiles as tuples: [(fname, dst_x, dst_y), ...]
    vrt_outfile: vrt output file
    """

    dst_list = list()
    vrt_content = list()
    rastercount_sav = None
    bandtype_sav = None

    for tile in tiles_list:
        fname, dst_x, dst_y = tile
        dataset = gdal.Open(fname)
        dst_w = dataset.RasterXSize
        dst_h = dataset.RasterYSize
        dst_list.append([dst_x, dst_y, dst_w, dst_h])

        rastercount = dataset.RasterCount
        bandtype = gdal.GetDataTypeName(dataset.GetRasterBand(1).DataType)

        rastercount_sav = rastercount if rastercount_sav is None else rastercount_sav
        bandtype_sav = bandtype if bandtype_sav is None else bandtype_sav

        assert rastercount_sav == rastercount
        assert bandtype_sav == bandtype

        while len(vrt_content) < rastercount:
            if vrt_content:
                vrt_content.append([vrt_bandheader(band=len(vrt_content)+1,
                                                   dataType=bandtype)])
            else:
                vrt_content.append([])

        for band in range(rastercount):
            vrt_content[band].append(vrt_body_source(fname, band+1, 0, 0, dst_w, dst_h,
                                                     dst_x, dst_y, dst_w, dst_h))

    for band in range(rastercount-1):
        vrt_content[band].append(vrt_bandfooter())

    vrt_content = [item for sublist in vrt_content for item in sublist]

    dst_numpy = np.array(dst_list)
    vrt_sizex = np.amax(dst_numpy[:, 2] + dst_numpy[:, 0])
    vrt_sizey = np.amax(dst_numpy[:, 3] + dst_numpy[:, 1])

    vrt_content.insert(0, vrt_header(vrt_sizex, vrt_sizey,
                                     dataType=bandtype))

    vrt_content.append(vrt_footer())

    with open(vrt_outfile, 'w') as vrt_file:
        vrt_file.write(' '.join((vrt_content)))


def main(tiles_file, outfile, sub_img, nbBands=1, dataType='Float32', color=False):
    outfile_basename = os.path.basename(outfile)
    outfile_dirname = os.path.dirname(outfile) or '.'

    output_format = outfile_basename[-3:]

    # If output format is tif, we need to generate a temporary vrt
    # with the same name
    vrt_basename = outfile_basename

    if output_format == 'tif' or output_format == 'png':
        vrt_basename = vrt_basename[:-3] + 'vrt'
    elif output_format != 'vrt':
        print('Error: only vrt or tif or png extension is allowed for output image.')
        return

    vrt_name = os.path.join(outfile_dirname, vrt_basename)

    # Read the tiles file
    if isinstance(tiles_file, list):
        tiles = tiles_file
    else:
        tiles = read_tiles(tiles_file)


    # Compute the global extent of the output image
    (min_x, max_x, min_y, max_y) = global_extent(tiles)

    # Now, write all row vrts
    vrt_row=write_row_vrts(outfile_dirname, tiles, sub_img, vrt_basename, min_x, max_x,
                           nbBands=nbBands, color=color, dataType=dataType)

    # Finally, write main vrt
    write_main_vrt(vrt_row, vrt_name, min_x, max_x, min_y, max_y, nbBands=nbBands, color=color, dataType=dataType)

    # If Output format is tif, convert vrt file to tif
    if output_format == 'tif':
        try:
            devnull = open(os.devnull, 'w')
            cmd = ['gdal_translate', '-ot', 'Float32', '-co', 'TILES=YES', '-co', 'BIGTIFF=IF_NEEDED',
                   '{}'.format(vrt_name),
                   '{}'.format(outfile)]
            subprocess.check_call(cmd, stdout=devnull, stderr=subprocess.STDOUT, env=os.environ)
        except:
            raise

        garbage.append(vrt_name)
        garbage_cleanup()


FORMAT = ['Float32', 'Byte']

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=('mosaic tool'))

    parser.add_argument('tiles', metavar='tiles.txt',
                        help=('path to the tiles.txt file'))
    parser.add_argument('outfile', metavar='out.tif',
                        help=('path to the output file.'
                              ' File extension can be .tif or .vrt'))
    parser.add_argument('sub_img', metavar='pair_1/height_map.tif',
                        help=('path to the sub-image to mosaic.'
                              ' Can be (but not limited to) height_map.tif,'
                              ' pair_n/height_map.tif, pair_n/rpc_err.tif,'
                              ' cloud_water_image_domain_mask.png.'
                              ' Note that rectified_* files CAN NOT be used.'))
    parser.add_argument('--format', type=str, choices=FORMAT,
                        default='Float32')
    parser.add_argument('--color', action='store_true', help=('deactivate color gray interpretation'))
    args = parser.parse_args()

    main(args.tiles, args.outfile, args.sub_img, dataType=args.format, color=args.color)