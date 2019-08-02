#!/usr/bin/env python
# -*- coding: iso-8859-15 -*-

# Copyright (C) 2019 Centre National d'Etudes Spatiales (CNES)

"""
Mainly contains the Partition class. A partition defines a way to partition the DEMs alti differences.
TODO add comment Fusion_partition
"""

import os
import collections
import numpy as np
import logging
from osgeo import gdal
import itertools

from .output_tree_design import get_out_dir
from .a3d_georaster import A3DGeoRaster

class NotEnoughDataToPartitionError(Exception):
    pass


class Partition:
    class LackOfPartitionDataError(Exception):
        def __init__(self):
            logging.error('At least one partition support must be provided '
                          '(shall it be linked to the reference DSM or the slave one). '
                          'Use the \'ref\' and/or \'dsm\' keys respectively.')

    # Only kind of partition supported
    # TODO see if two classes will not be better
    type = ["to_be_classification_layers", "classification_layers"]

    # default no data value
    nodata = -32768.0

    ####### initialization #######
    def __init__(self, name, partition_kind, coreg_dsm, coreg_ref, outputDir, **cfg_layer):
        # Sanity check
        if partition_kind in self.type:
            self._type_layer = partition_kind
        else:
            logging.error('Unsupported partition kind {}. Try one of the following {}'.format(partition_kind, self.type))
            raise KeyError

        # Get partition name
        self._name = name

        # Create output dir (where to store partition results & data)
        self._output_dir = os.path.join(outputDir, get_out_dir('stats_dir'), self._name)
        self.create_output_dir()

        # Store coreg path (TODO why?)
        self.coreg_path = {'ref': None, 'dsm': None}
        self._coreg_shape = coreg_ref.r.shape
        self.coreg_path['dsm'] = coreg_dsm
        self.coreg_path['ref'] = coreg_ref

        # Store classes (TODO why?)
        self._classes = {}
        if 'classes' in cfg_layer:
            self._classes = collections.OrderedDict(cfg_layer['classes'])
        elif 'ranges' in cfg_layer:
            # transform 'ranges' to 'classes'
            self._classes = self.generate_classes(cfg_layer['ranges'])
        else:
            logging.error('Neither classes nor ranges where given as input sets to partition the stats')
            raise KeyError

        # Store path to initial layer
        self.ref_path = ''
        self.dsm_path = ''
        if 'ref' in cfg_layer:
            self.ref_path = cfg_layer['ref']
        if 'dsm' in cfg_layer:
            self.dsm_path = cfg_layer['dsm']
        if (not 'ref' in cfg_layer) and (not 'dsm' in cfg_layer):
            raise self.LackOfPartitionDataError
        if (not cfg_layer['ref']) and (not cfg_layer['dsm']):
            if self.type_layer == "classification_layers":
                raise self.LackOfPartitionDataError
            else:
                if self.name != 'slope':
                    raise self.LackOfPartitionDataError
                else:
                    # create slope : ref and dsm
                    self.create_slope(coreg_dsm, coreg_ref)

        # Create the layer map
        self.map_path = {'ref': None, 'dsm': None}
        if self.type_layer == "to_be_classification_layers":
            # if the partition is not yet a labelled map, then make it so
            if self.ref_path:
                self.create_map(self.ref_path, 'ref')
            if self.dsm_path:
                self.create_map(self.dsm_path, 'dsm')
        elif self.type_layer == "classification_layers":
            if 'ref' in cfg_layer:
                self.map_path['ref'] = cfg_layer['ref']
            if 'dsm' in cfg_layer:
                self.map_path['dsm'] = cfg_layer['dsm']

        # Reproj the layer map
        self.reproject_path = {'ref': None, 'dsm': None}
        self.rectify_map()

        # Create the sets based on the layer map
        self._fill_sets_attributes()

        logging.info('Partition created as:', self)

    ####### getters and setters #######
    @property
    def out_dir(self):
        return self._output_dir

    @property
    def coreg_shape(self):
        return self._coreg_shape

    @property
    def name(self):
        return self._name

    @property
    def type_layer(self):
        return self._type_layer

    @property
    def classes(self):
        return self._classes

    @property
    def sets_names(self):
        return self._sets_names

    @property
    def sets_labels(self):
        return self._sets_labels

    @property
    def sets_colors(self):
        return self._sets_colors

    @property
    def sets_indexes_ref(self):
        return self._sets_indexes['ref']

    @property
    def sets_indexes_dsm(self):
        return self._sets_indexes['dsm']

    @property
    def sets_masks(self):
        masks = [np.ones(self.coreg_shape)*False for i in range(2)]
        #TODO [0] = ref then we need to iter over labels
        masks[0][self.sets_indexes_ref] = True
        masks[1][self.sets_indexes_dsm] = True
        return masks

    def __repr__(self):
        return "self.name : {}\n, self.type_layer : {}\n, self.ref_path : {}\n, self.dsm_path : {}\n, " \
               "self.reproject_path : {}\n, self.map_path : {}\n, self.classes : {}\n, self.coreg_path : {}\n," \
               "self.sets_colors : {} \n".format(
            self.name, self.type_layer, self.ref_path, self.dsm_path,
            self.reproject_path, self.map_path, self.classes, self.coreg_path, self.sets_colors)

    def generate_classes(self, ranges):
        # change the intervals into a list to make 'classes' generic
        classes = collections.OrderedDict()
        for idx in range(0, len(ranges)):
            if idx == len(ranges) - 1:
                if self.name == 'slope':
                    key = "[{}%;inf[".format(ranges[idx])
                else:
                    key = "[{};inf[".format(ranges[idx])
            else:
                if self.name == 'slope':
                    key = "[{}%;{}%[".format(ranges[idx], ranges[idx + 1])
                else:
                    key = "[{};{}[".format(ranges[idx], ranges[idx + 1])
            classes[key] = ranges[idx]

        return classes

    def create_output_dir(self):
        """
        Create folder stats results
        :param name_layer: layer name
        :return:
        """
        os.makedirs(self._output_dir, exist_ok=True)

    def create_slope(self, coreg_dsm, coreg_ref):
        """
        Create slope if not exist
        :param coreg_dsm: A3DDEMRaster, input coregistered DSM
        :param coreg_ref: A3DDEMRaster, input coregistered REF
        :param name_layer: layer name
        :return:
        """
        # Compute ref slope
        self.ref_path = os.path.join(self._output_dir, 'Ref_support.tif')
        slope_ref, aspect_ref = coreg_ref.get_slope_and_aspect(degree=False)
        slope_ref_georaster = A3DGeoRaster.from_raster(slope_ref,
                                                       coreg_ref.trans,
                                                       "{}".format(coreg_ref.srs.ExportToProj4()),
                                                       nodata=self.nodata)
        slope_ref_georaster.save_geotiff(self.ref_path)

        # Compute dsm slope
        self.dsm_path = os.path.join(self._output_dir, 'DSM_support.tif')
        slope_dsm, aspect_dsm = coreg_dsm.get_slope_and_aspect(degree=False)
        slope_dsm_georaster = A3DGeoRaster.from_raster(slope_dsm,
                                                       coreg_dsm.trans,
                                                       "{}".format(coreg_dsm.srs.ExportToProj4()),
                                                       nodata=self.nodata)
        slope_dsm_georaster.save_geotiff(self.dsm_path)

    def create_map(self, slope_img, type_slope):
        """
        Create the map for each slope (l'intervalle des valeurs est transforme en 1 valeur (la min de l'intervalle))
        :param slope_img: slope image path
        :param type_slope: type of slope : 'ref' or 'dsm'
        :return:
        """
        # use radiometric ranges to classify
        slope = A3DGeoRaster(slope_img)
        rad_range = list(self.classes.values())
        map_img = A3DGeoRaster.from_raster(np.ones(slope.r.shape) * self.nodata,
                                           slope.trans, "{}".format(slope.srs.ExportToProj4()), nodata=self.nodata)
        for idx in range(0, len(rad_range)):
            if idx == len(rad_range) - 1:
                map_img.r[np.where((slope.r >= rad_range[idx]))] = rad_range[idx]
            else:
                map_img.r[np.where((slope.r >= rad_range[idx]) & (slope.r < rad_range[idx + 1]))] = rad_range[idx]

        self.map_path[type_slope] = os.path.join(self._output_dir, type_slope + '_support_map.tif')
        map_img.save_geotiff(self.map_path[type_slope])

    def _create_set_indices(self):
        """
        Returns a list of numpy.where, by class. Each element defines a set. The sets partition / classify the image.
        Each numpy.where contains the coordinates of the sets of the class.
        Create list of coordinates arrays :
            -> self.sets_indices = [(label_name, np.where(...)), ... label_name, np.where(...))] ,

        :param classes: ordered dict of labels and associated values
        :return:
        """
        dsm_supports = ['ref', 'dsm']
        sets_indices = {support: None for support in dsm_supports }
        for support in dsm_supports:
            if self.reproject_path[support]:
                img_to_classify = A3DGeoRaster(self.reproject_path[support])
                sets_indices[support] = []
                # calculate sets_indices of partition
                for class_name, class_value in self.classes.items():
                    if isinstance(class_value, list):
                        elm = (class_name, np.where(np.logical_or(*[np.equal(img_to_classify.r, label_i)
                                                                     for label_i in class_value])))
                    else:
                        elm = (class_name, np.where(img_to_classify.r == class_value))
                    sets_indices[support].append(elm)
        return sets_indices

    def _fill_sets_attributes(self):
        """

        :param classes: ordered dict of labels and associated values
        :return:
        """

        # fill sets_labels & sets_names
        if self.name == 'slope':
            self._sets_names = self.classes.keys()
            # Slope labels are historically customized
            self._sets_labels = [r'$\nabla$ > {}%'.format(self.classes[set_name])
                                 if set_name.endswith('inf[') else r'$\nabla \in$ {}'.format(set_name)
                                 for set_name in self._sets_names]
        else:
            self._sets_labels = self.classes.keys()
            self._sets_names = ['{}:{}'.format(key, value) for key, value in self.classes.items()]
            #self._sets_names = [name.replace(',', ';') for name in self._sets_names]

        # fill sets_colors
        self._sets_colors = np.multiply(getColor(len(self.sets_names)), 255) / 255

        # fill sets_indexes
        tuples_of_labels_and_indexes = self._create_set_indices()
        self._sets_indexes = {'ref': (np.array([], dtype=np.uint8), np.array([], dtype=np.uint8)),
                              'dsm': (np.array([], dtype=np.uint8), np.array([], dtype=np.uint8))}
        if tuples_of_labels_and_indexes['ref']:
            self._sets_indexes['ref'] = [item[1] for item in tuples_of_labels_and_indexes['ref']]
        if tuples_of_labels_and_indexes['dsm']:
            self._sets_indexes['dsm'] = [item[1] for item in tuples_of_labels_and_indexes['dsm']]

    def rectify_map(self):
        """
        Reproject  the layer maps on top of coreg dsm and coreg ref (which are coregistered together)

        :return:
        """
        for map_name, map_path in self.map_path.items():
            if map_path:
                map_img = A3DGeoRaster(map_path)
                rectified_map = map_img.reproject(self.coreg_path[map_name].srs, int(self.coreg_path[map_name].nx),
                                                  int(self.coreg_path[map_name].ny), self.coreg_path[map_name].footprint[0],
                                                  self.coreg_path[map_name].footprint[3],
                                                  self.coreg_path[map_name].xres, self.coreg_path[map_name].yres,
                                                  nodata=map_img.nodata, interp_type=gdal.GRA_NearestNeighbour)
                self.reproject_path[map_name] = os.path.join(self._output_dir,
                                                             map_name + '_support_map_rectif.tif')
                rectified_map.save_geotiff(self.reproject_path[map_name])


class Fusion_partition(Partition):

    def __init__(self, partitions, outputDir):
        """
        TODO Merge the layers to generate the layers fusion
        :param partitions: list d objet Partition
        :return: TODO
        """
        #TODO find a way to call __init__ of Partition
        if len(partitions) == 1:
            logging.error('There must be at least 2 partitions to be merged together')
            raise NotEnoughDataToPartitionError

        logging.info('{}'.format([p.reproject_path['ref'] for p in partitions]))
        dict_fusion = {'ref': np.all([p.reproject_path['ref'] is not None for p in partitions]),
                       'dsm': np.all([p.reproject_path['dsm'] is not None for p in partitions])}
        if ~(dict_fusion['ref'] + dict_fusion['dsm']) :
            logging.error('For the partition to be merged, there must be at least one support (ref or dsm) '
                          'provided by every partition')
            raise NotEnoughDataToPartitionError

        self.ref_path = ''
        self.dsm_path = ''
        self.coreg_path = {'ref': None, 'dsm': None}
        self.reproject_path = {'ref': None, 'dsm': None}
        self.nodata = -32768.0
        # sets ==> TODO utiliser _fill_sets_attributs
        self._sets_indexes = {'ref': (np.array([], dtype=np.uint8), np.array([], dtype=np.uint8)),
                              'dsm': (np.array([], dtype=np.uint8), np.array([], dtype=np.uint8))}
        self._sets_names = None
        self._sets_labels = None
        self.map_path = {'ref': None, 'dsm': None}

        self._classes = {}
        all_combi_labels = None

        # create folder stats results fusion si layers_ref_flag ou layers_dsm_flag est � True
        # =====> On ne cree pas le dossier de sortie de la couche fusionn�
        self._name = 'fusion_layer'
        self._output_dir = os.path.join(outputDir, get_out_dir('stats_dir'), self._name)
        self._type_layer = 'classification_layers'
        self.create_output_dir()

        # Boucle sur [ref, dsm]
        #   Boucle sur chaque layer
        #       S'il y a plusieurs des layers donn�es ou calcul�es
        #           ==> pour calculer les masks de chaque label
        #           ==> calculer toutes les combinaisons (developpement des labels entre eux mais pas les listes)
        #           ==> puis calculer les masks fusionn�s (a associer avec les bons labels)
        #           ==> generer la nouvelle image classif (fusion) avec de nouveaux labels calcul�s arbitrairement et li�s aux labels d entrees

        for df_k, df_v in dict_fusion.items():
            if df_v:
                # get les reproject_ref/dsm et faire une nouvelles map avec son dictionnaire associ�
                clayers_to_fusion_path = [(parti.name, parti.reproject_path[df_k]) for parti in partitions]

                # lire les images clayers_to_fusion
                clayers_to_fusion = [(k, A3DGeoRaster(cltfp)) for k, cltfp in clayers_to_fusion_path]
                classes_to_fusion = []
                for partition in partitions:
                    classes_to_fusion.append([(partition.name, cl_classes_label) for cl_classes_label in partition.classes.keys()])

                if not (all_combi_labels and self._classes):
                    all_combi_labels, self._classes = create_new_classes(classes_to_fusion)

                # create la layer fusionnee + les sets assossi�s
                sets_masks = {}
                for parti in partitions:
                    sets_def_indices = parti._create_set_indices()
                    sets_masks[parti.name] = dict(sets_def_indices[df_k])

                map_fusion, self._sets_indexes[df_k], self._sets_colors = \
                    create_fusion(sets_masks, all_combi_labels, self._classes, clayers_to_fusion[0][1])

                # save map_fusion
                self.map_path[df_k] = os.path.join(self._output_dir, '{}_fusion_layer.tif'.format(df_k))
                map_fusion.save_geotiff(self.map_path[df_k])

        logging.info('Partition FUSION created as:', self)


#####################################################
######  fonction n'appartenant pas � la classe ######
#####################################################
def create_new_classes(classes_to_fusion):
    """
    Generate the 'classes' dictionary for merged layers
    :param classes_to_fusion: list of classes to merge
    :return: TODO list of combinations of labels, new classes
    """
    # calcul toutes les combinaisons (developpement des labels entre eux)
    all_combi_labels = list(itertools.product(*classes_to_fusion))

    new_label_value = 1
    new_classes = {}
    for combi in all_combi_labels:
        # creer le new label dans le dictionnaire new_classes
        new_label_name = '&'.join(['@'.join(elm_combi) for elm_combi in combi])
        new_classes[new_label_name] = new_label_value
        new_label_value += 1

    return all_combi_labels, new_classes


def getColor(nb_color=10):
    import matplotlib
    import matplotlib.pyplot as P
    if 10 < nb_color < 21:
        if matplotlib.__version__ >= '2.0.1':
            # According to matplotlib documentation the Vega colormaps are deprecated since the 2.0.1 and
            # disabled since 2.2.0
            x = P.cm.get_cmap('tab20')
        else:
            x = P.cm.get_cmap('Vega20')
    if nb_color < 11:
        if matplotlib.__version__ >= '2.0.1':
            x = P.cm.get_cmap('tab10')
        else:
            x = P.cm.get_cmap('Vega10')
    if nb_color > 20:
        raise NameError("Error : Too many colors requested")

    return np.array(x.colors)


def create_fusion(sets_masks, all_combi_labels, classes_fusion, layers_obj):
    """
    TODO create la fusion de toute les maps
    :param sets_masks: dict par layer (exemple 'slope', 'carte_occupation', ...) contentant chacun une liste de tuple,
                        dont chaque tuple contient ('nom_label', A3DGeoRaster_mask)
    layers_obj: une layer d'exemple pour recuperer la taille et le georef
    :return:
    """
    # create map qui fusionne toutes les combinaisons de classes
    map_fusion = np.ones(layers_obj.r.shape) * -32768.0
    sets_fusion = []
    sets_colors = np.multiply(getColor(len(all_combi_labels)), 255)
    # recupere les masques associ�es aux tuples
    for combi in all_combi_labels:
        #dict_elm_to_fusion = {}
        mask_fusion = np.ones(layers_obj.r.shape)
        for elm_combi in combi:
            layer_name = elm_combi[0]
            label_name = elm_combi[1]
            # recupere le mask associ� au label_name
            #dict_elm_to_fusion[layer_name] = {}
            #dict_elm_to_fusion[layer_name][label_name] = sets_masks[layer_name]['sets_def'][label_name]
            # concatene les masques des differentes labels du tuple/combi dans mask_fusion
            mask_label = np.zeros(layers_obj.r.shape)
            mask_label[sets_masks[layer_name][label_name]] = 1              # TODO change sets_masks[layer_name]['sets_def'][label_name] le dictionnaire n'est plus le meme => une liste mtn
            mask_fusion = mask_fusion * mask_label

        # recupere le new label associ� dans ls dictionnaire new_classes
        new_label_name = '&'.join(['@'.join(elm_combi) for elm_combi in combi])
        new_label_value = classes_fusion[new_label_name]
        map_fusion[np.where(mask_fusion)] = new_label_value
        # SAVE mask_fusion
        sets_fusion.append((new_label_name, np.where(mask_fusion)))

    # save map fusionne
    map = A3DGeoRaster.from_raster(map_fusion,layers_obj.trans,
                                   "{}".format(layers_obj.srs.ExportToProj4()), nodata=-32768)

    return map, sets_fusion, sets_colors / 255.