"""
This Python package regionalizes processes from the ecoinvent database using trade date from the UN COMTRADE database.
In a first time (that I call first_order_regionalization) electricity, heat and municipal solid waste processes inputs
are adapted to the geographical context. In a second time, all created processes are linked to the rest of the database.

file name: regioinvent.py
author: Maxime Agez
e-mail: maxime.agez@polymtl.ca
date created: 06-04-24
"""

import pandas as pd
import numpy as np
import json
import pkg_resources
import brightway2 as bw2
import uuid
import sqlite3
import logging
import sys
import os
import pickle
import collections
import wurst
import wurst.searching as ws
import copy
from tqdm import tqdm


class Regioinvent:
    def __init__(self, trade_database_path, regioinvent_database_name, bw_project_name, ecoinvent_database_name):

        # set up logging tool
        self.logger = logging.getLogger('Regioinvent')
        self.logger.setLevel(logging.INFO)
        self.logger.handlers = []
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
        self.logger.propagate = False

        bw2.projects.set_current(bw_project_name)
        self.regioinvent_database_name = regioinvent_database_name
        self.ecoinvent_database_name = ecoinvent_database_name
        self.new_regionalized_ecoinvent_database_name = ecoinvent_database_name + ' biosphere flows regionalized'
        self.name_regionalized_biosphere_database = 'biosphere3_regionalized_flows'
        self.trade_conn = sqlite3.connect(trade_database_path)
        self.ecoinvent_conn = sqlite3.connect(bw2.Database(self.regioinvent_database_name).filepath_processed().split(
            '\\processed\\')[0] + '\\lci\\databases.db')
        self.ecoinvent_cursor = self.ecoinvent_conn.cursor()
        self.multiple_activities_per_product = []
        self.no_glo_region = []
        self.assigned_random_geography = []

        with open(pkg_resources.resource_filename(__name__, '/Data/ecoinvent_to_HS.json'), 'r') as f:
            self.eco_to_hs_class = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/HS_to_exiobase_name.json'), 'r') as f:
            self.hs_class_to_exio = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/country_to_ecoinvent_regions.json'), 'r') as f:
            self.country_to_ecoinvent_regions = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/electricity_processes.json'), 'r') as f:
            self.electricity_geos = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/electricity_aluminium_processes.json'), 'r') as f:
            self.electricity_aluminium_geos = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/waste_processes.json'), 'r') as f:
            self.waste_geos = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/water_processes.json'), 'r') as f:
            self.water_geos = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/heat_industrial_ng_processes.json'), 'r') as f:
            self.heat_district_ng = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/heat_industrial_non_ng_processes.json'), 'r') as f:
            self.heat_district_non_ng = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/heat_small_scale_non_ng_processes.json'), 'r') as f:
            self.heat_small_scale_non_ng = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/COMTRADE_to_ecoinvent_geographies.json'), 'r') as f:
            self.convert_ecoinvent_geos = json.load(f)
        with open(pkg_resources.resource_filename(__name__, '/Data/COMTRADE_to_exiobase_geographies.json'), 'r') as f:
            self.convert_exiobase_geos = json.load(f)

        self.created_geographies = dict.fromkeys(self.eco_to_hs_class.keys())
        self.unit = dict.fromkeys(self.eco_to_hs_class.keys())

        self.water_flows_in_ecoinvent = ['Water', 'Water, cooling, unspecified natural origin',
                                         'Water, lake',
                                         'Water, river',
                                         'Water, turbine use, unspecified natural origin',
                                         'Water, unspecified natural origin',
                                         'Water, well, in ground']

        # if self.name_regionalized_biosphere_database not in bw2.databases:
        #     self.logger.info("Creating regionalized biosphere flows...")
        #     self.create_regionalized_biosphere_flows()

        # if 'IMPACT World+ Damage 2.0.1_regionalized' not in [i[0] for i in list(bw2.methods)]:
        #     self.logger.info("Linking regionalized LCIA method to regionalized biosphere database...")
        #     self.link_regionalized_biosphere_to_regionalized_CFs()

        self.ei39_wurst = wurst.extract_brightway2_databases(self.ecoinvent_database_name, add_identifiers=True)

    def create_regionalized_biosphere_flows(self):

        locations_for_water_flows = []
        for act in [i for i in bw2.Database(self.ecoinvent_database_name)]:
            for exc in [i for i in act.biosphere()]:
                if 'name' in exc.as_dict():
                    if exc.as_dict()['name'] in self.water_flows_in_ecoinvent:
                        if act.as_dict()['location'] not in locations_for_water_flows:
                            locations_for_water_flows.append(act.as_dict()['location'])

        water_flows_to_create = []
        for water_flow_type in self.water_flows_in_ecoinvent:
            for location in locations_for_water_flows:
                water_flows_to_create.append(', '.join([water_flow_type, location]))

        biosphere_data = {}
        for water_flow in water_flows_to_create:
            for location in locations_for_water_flows:
                if len(water_flow.split(', ' + location)) > 1 and water_flow.split(', ' + location)[1] == '':
                    if ','.join(water_flow.split(', ' + location)[:-1]) != 'Water':
                        # unique identifier
                        unique_id = uuid.uuid4().hex
                        # format data into brightway-readable format
                        biosphere_data[(self.name_regionalized_biosphere_database, unique_id)] = {
                            "name": water_flow,
                            "unit": 'cubic meter',
                            "type": 'emission',
                            "categories": ('natural resource', 'in water'),
                            "code": unique_id}
                        if ','.join(water_flow.split(', ' + location)[:-1]) == 'Water, unspecified natural origin':
                            unique_id = uuid.uuid4().hex
                            biosphere_data[(self.name_regionalized_biosphere_database, unique_id)] = {
                                "name": water_flow,
                                "unit": 'cubic meter',
                                "type": 'emission',
                                "categories": ('natural resource', 'in ground'),
                                "code": unique_id}
                            unique_id = uuid.uuid4().hex
                            biosphere_data[(self.name_regionalized_biosphere_database, unique_id)] = {
                                "name": water_flow,
                                "unit": 'cubic meter',
                                "type": 'emission',
                                "categories": ('natural resource', 'fossil well'),
                                "code": unique_id}
                    else:
                        for subcomp in ['surface water', 'ground-', 'ground-, long-term', 'fossil well']:
                            # unique identifier
                            unique_id = uuid.uuid4().hex
                            # format data into brightway-readable format
                            biosphere_data[(self.name_regionalized_biosphere_database, unique_id)] = {
                                "name": water_flow,
                                "unit": 'cubic meter',
                                "type": 'emission',
                                "categories": ('water', subcomp),
                                "code": unique_id}

                        # specific for unspecified subcomp
                        unique_id = uuid.uuid4().hex
                        biosphere_data[(self.name_regionalized_biosphere_database, unique_id)] = {
                            "name": water_flow,
                            "unit": 'cubic meter',
                            "type": 'emission',
                            "categories": ('water',),
                            "code": unique_id}

        bw2.Database(self.name_regionalized_biosphere_database).write(biosphere_data)

    def create_ecoinvent_with_regionalized_biosphere_flows(self):

        relevant_subcomps = [('natural resource', 'in water'), ('natural resource', 'in ground'),
                             ('natural resource', 'fossil well'),
                             ('water',), ('water', 'surface water'), ('water', 'ground-'),
                             ('water', 'ground-, long-term'),
                             ('water', 'fossil well')]

        data = {}
        for act in [i for i in bw2.Database(self.ecoinvent_database_name)]:
            # first, create all activities with modified metadata
            dict_activity = act.as_dict().copy()
            dict_activity['database'] = self.new_regionalized_ecoinvent_database_name
            data[(self.new_regionalized_ecoinvent_database_name, act.key[1])] = dict_activity
            data[(self.new_regionalized_ecoinvent_database_name, act.key[1])]['exchanges'] = []

            # second, create the exchanges with new regionalized water flows
            for exc in [i for i in act.exchanges()]:
                # check exchange has a name
                if 'name' in exc.as_dict():
                    # check exchange is a water flow
                    if exc.as_dict()['name'] in self.water_flows_in_ecoinvent:
                        # check it's a water flows in water comp and not ocean subcomp
                        if (bw2.Database('biosphere3').get(exc.as_dict()['flow']).as_dict()['categories'] in relevant_subcomps):

                            # identify the regionalized water flow ID
                            new_water_flow_id = [i for i in bw2.Database(self.name_regionalized_biosphere_database) if (
                                    i['name'] == exc.as_dict()['name'] + ', ' + act.as_dict()['location'] and
                                    i['categories'] == bw2.Database('biosphere3').get(
                                exc.as_dict()['flow']).as_dict()['categories'])][0].key

                            # add the exchange to the list
                            exc.as_dict()['flow'] = new_water_flow_id[1]
                            exc.as_dict()['input'] = new_water_flow_id
                            exc.as_dict()['name'] = exc.as_dict()['name'] + ', ' + act.as_dict()['location']
                            exc.as_dict()['output'] = (self.new_regionalized_ecoinvent_database_name, act.key[1])
                            data[(self.new_regionalized_ecoinvent_database_name, act.key[1])]['exchanges'].append(
                                exc.as_dict())

                        else:
                            # if it's not a regionalized water flow exchange, just copy paste the exchange
                            exc.as_dict()['output'] = (self.new_regionalized_ecoinvent_database_name, act.key[1])
                            data[(self.new_regionalized_ecoinvent_database_name, act.key[1])]['exchanges'].append(
                                exc.as_dict())
                    else:
                        # if it's not a regionalized water flow exchange, just copy paste the exchange
                        if exc.as_dict()['type'] == 'biosphere':
                            exc.as_dict()['input'] = ('biosphere3', exc.as_dict()['input'][1])
                        else:
                            exc.as_dict()['input'] = (
                            self.new_regionalized_ecoinvent_database_name, exc.as_dict()['input'][1])
                        exc.as_dict()['output'] = (self.new_regionalized_ecoinvent_database_name, act.key[1])
                        data[(self.new_regionalized_ecoinvent_database_name, act.key[1])]['exchanges'].append(exc.as_dict())
                else:
                    # if it's not a regionalized water flow exchange, just copy paste the exchange
                    if exc.as_dict()['type'] == 'biosphere':
                        exc.as_dict()['input'] = ('biosphere3', exc.as_dict()['input'][1])
                    else:
                        exc.as_dict()['input'] = (self.new_regionalized_ecoinvent_database_name, exc.as_dict()['input'][1])
                    exc.as_dict()['output'] = (self.new_regionalized_ecoinvent_database_name, act.key[1])
                    data[(self.new_regionalized_ecoinvent_database_name, act.key[1])]['exchanges'].append(exc.as_dict())

        bw2.Database(self.new_regionalized_ecoinvent_database_name).write(data)

    def link_regionalized_biosphere_to_regionalized_CFs(self):

        # load regular IW+
        ei_iw = pd.read_excel(pkg_resources.resource_filename(
            __name__, '/Data/impact_world_plus_2.0.1_expert_version_ecoinvent_v39.xlsx')).drop('Unnamed: 0', axis=1)

        not_regio_bw_flows_with_codes = (pd.DataFrame(
                [(i.as_dict()['name'], i.as_dict()['categories'][0], i.as_dict()['categories'][1], i.as_dict()['code'])
                 if len(i.as_dict()['categories']) == 2
                 else (i.as_dict()['name'], i.as_dict()['categories'][0], 'unspecified', i.as_dict()['code'])
                 for i in bw2.Database('biosphere3')],
                columns=['Elem flow name', 'Compartment', 'Sub-compartment', 'code']))

        ei_iw_db = ei_iw.merge(not_regio_bw_flows_with_codes, on=['Elem flow name', 'Compartment', 'Sub-compartment'])

        # make regionalized iw+
        iw = pd.read_excel(pkg_resources.resource_filename(
            __name__, '/Data/impact_world_plus_2.0.1_water_categories.xlsx')).drop(['Unnamed: 0'], axis=1)
        regio_bw_flows_with_codes = (pd.DataFrame(
                [(i.as_dict()['name'], i.as_dict()['categories'][0], i.as_dict()['categories'][1], i.as_dict()['code'])
                 if len(i.as_dict()['categories']) == 2
                 else (i.as_dict()['name'], i.as_dict()['categories'][0], 'unspecified', i.as_dict()['code'])
                 for i in bw2.Database(self.name_regionalized_biosphere_database)],
                columns=['Elem flow name', 'Compartment', 'Sub-compartment', 'code']))

        with open(pkg_resources.resource_filename(__name__, '/Data/comps.json'), 'r') as f:
            comps = json.load(f)

        with open(pkg_resources.resource_filename(__name__, '/Data/subcomps.json'), 'r') as f:
            subcomps = json.load(f)

        iw.Compartment = [comps[i] for i in iw.Compartment]
        iw.loc[:, 'Sub-compartment'] = [subcomps[i] for i in iw.loc[:, 'Sub-compartment']]

        # special cases: fossil well subcomp in water comp = ground- subcomp
        df = iw.loc[[i for i in iw.index if (iw.loc[i, 'Sub-compartment'] == 'ground-' and
                                             iw.loc[i, 'Compartment'] == 'water')]].copy()
        df.loc[:, 'Sub-compartment'] = 'fossil well'
        iw = pd.concat([iw, df])
        iw = clean_up_dataframe(iw)

        # special cases: fossil well subcomp in raw comp = in ground subcomp
        df = iw.loc[[i for i in iw.index if (iw.loc[i, 'Sub-compartment'] == 'in ground' and
                                             iw.loc[i, 'Compartment'] == 'natural resource')]].copy()
        df.loc[:, 'Sub-compartment'] = 'fossil well'
        iw = pd.concat([iw, df])
        iw = clean_up_dataframe(iw)

        concordance = pd.read_excel(pkg_resources.resource_filename(__name__, '/Data/concordance.xlsx'))
        concordance = dict(list(zip(concordance.loc[:, 'Name ecoinvent'], concordance.loc[:, 'Name iw+'])))
        regio_bw_flows_with_codes.loc[:, 'Elem flow name'] = [
            concordance[i] for i in regio_bw_flows_with_codes.loc[:, 'Elem flow name']]

        ei_in_bw = pd.concat(
            [ei_iw_db, iw.merge(regio_bw_flows_with_codes, on=['Elem flow name', 'Compartment', 'Sub-compartment'])])
        ei_in_bw.set_index(['Impact category', 'CF unit'], inplace=True)

        # make total HH and total EQ categories
        total_HH = ei_in_bw.loc(axis=0)[:, 'DALY'].groupby(
            by=['code', 'Compartment', 'Sub-compartment', 'Elem flow name', 'CAS number',
                'Elem flow unit', 'MP or Damage']).sum().reset_index()
        total_HH.loc[:, 'Impact category'] = 'Total human health'
        total_HH.loc[:, 'CF unit'] = 'DALY'
        total_HH = total_HH.set_index(['Impact category', 'CF unit'])

        total_EQ = ei_in_bw.loc(axis=0)[:, 'PDF.m2.yr'].groupby(
            by=['code', 'Compartment', 'Sub-compartment', 'Elem flow name', 'CAS number',
                'Elem flow unit', 'MP or Damage']).sum().reset_index()
        total_EQ.loc[:, 'Impact category'] = 'Total ecosystem quality'
        total_EQ.loc[:, 'CF unit'] = 'PDF.m2.yr'
        total_EQ = total_EQ.set_index(['Impact category', 'CF unit'])

        ei_in_bw = pd.concat([ei_in_bw, total_HH, total_EQ])

        impact_categories = ei_in_bw.index.drop_duplicates()
        for ic in impact_categories:
            if ei_in_bw.loc[[ic], 'MP or Damage'].iloc[0] == 'Midpoint':
                mid_end = 'Midpoint'
                # create the name of the method
                name = ('IMPACT World+ ' + mid_end + ' ' + '2.0.1_regionalized', 'Midpoint', ic[0])
            else:
                mid_end = 'Damage'
                # create the name of the method
                if ic[1] == 'DALY':
                    name = ('IMPACT World+ ' + mid_end + ' ' + '2.0.1_regionalized', 'Human health', ic[0])
                else:
                    name = ('IMPACT World+ ' + mid_end + ' ' + '2.0.1_regionalized', 'Ecosystem quality', ic[0])

            # initialize the "Method" method
            new_method = bw2.Method(name)
            # register the new method
            new_method.register()
            # set its unit
            new_method.metadata["unit"] = ic[1]

            df = ei_in_bw.loc[[ic], ['code', 'CF value']].copy()
            df.set_index('code', inplace=True)

            data = []
            biosphere3 = [i.as_dict()['code'] for i in bw2.Database('biosphere3')]
            biosphere3_regio = [i.as_dict()['code'] for i in bw2.Database(self.name_regionalized_biosphere_database)]
            for stressor in df.index:
                if stressor in biosphere3:
                    data.append((('biosphere3', stressor), df.loc[stressor, 'CF value']))
                elif stressor in biosphere3_regio:
                    data.append(((self.name_regionalized_biosphere_database, stressor), df.loc[stressor, 'CF value']))
            new_method.write(data)

    def first_order_regionalization(self):

        print("Regionalizing main inputs of traded products of ecoinvent")
        for product in tqdm(self.eco_to_hs_class, leave=True):
            cmd_export_data = self.export_data[self.export_data.cmdCode.isin([self.eco_to_hs_class[product]])].copy(
                'deep')
            # calculate the average export volume for each country
            cmd_export_data = cmd_export_data.groupby('reporterISO').agg({'qty': 'mean'})
            exporters = (cmd_export_data.qty / cmd_export_data.qty.sum()).sort_values(ascending=False)
            # only keep the countries representing 99% of global exports of the product and create a RoW from that
            limit = exporters.index.get_loc(exporters[exporters.cumsum() > 0.99].index[0]) + 1
            remainder = exporters.iloc[limit:].sum()
            exporters = exporters.iloc[:limit]
            if 'RoW' in exporters.index:
                exporters.loc['RoW'] += remainder
            else:
                exporters.loc['RoW'] = remainder

            self.created_geographies[product] = [i for i in exporters.index]

            # select product
            filter_processes = ws.get_many(self.ei39_wurst, ws.equals("reference product", product),
                                           ws.exclude(ws.contains("name", "market")),
                                           ws.exclude(ws.contains("name", "import from")))

            available_geographies = []
            for dataset in filter_processes:
                available_geographies.append(dataset['location'])

            # copy to keep metadata
            global_market_activity = copy.deepcopy(dataset)

            # rename activity
            global_market_activity['name'] = f"""production market for {product}"""

            # add a comment
            global_market_activity['comment'] = f"""This process represents the global production market for {product}. The amounts come from export data from the UN 
            COMTRADE database. Data from UN COMTRADE is already in physical units. An average of the 5 last years of export trade available data is taken 
            (in general from 2019 to 2023). Countries are taken until 99% of the global production amounts are covered, the rest of the data is aggregated in a RoW 
            (Rest-of-the-World) region."""

            # location will be global (global market)
            global_market_activity['location'] = 'GLO'

            # new code needed
            global_market_activity['code'] = uuid.uuid4().hex

            # change database
            global_market_activity['database'] = self.regioinvent_database_name

            # reset exchanges with only the production exchange
            global_market_activity['exchanges'] = [{'amount': 1.0,
                                                    'type': 'production',
                                                    'product': global_market_activity['reference product'],
                                                    'name': global_market_activity['name'],
                                                    'unit': global_market_activity['unit'],
                                                    'location': global_market_activity['location'],
                                                    'database': self.regioinvent_database_name,
                                                    'code': global_market_activity['code'],
                                                    'input': (
                                                    global_market_activity['database'], global_market_activity['code']),
                                                    'output': (global_market_activity['database'],
                                                               global_market_activity['code'])}]
            self.unit[product] = global_market_activity['unit']

            def copy_process(product, region, export_country):
                try:
                    process = ws.get_one(self.ei39_wurst,
                                         ws.equals("reference product", product),
                                         ws.equals("location", region),
                                         ws.equals("database", self.ecoinvent_database_name),
                                         ws.exclude(ws.contains("name", "market")))
                except wurst.errors.MultipleResults:
                    self.multiple_activities_per_product.append(product)
                    return
                except wurst.errors.NoResults:
                    if region == 'GLO':
                        self.no_glo_region.append(product)
                        return
                    else:
                        print('aie, ' + product)
                regio_process = copy.deepcopy(process)
                # change location
                regio_process['location'] = export_country
                # change code
                regio_process['code'] = uuid.uuid4().hex
                # change database
                regio_process['database'] = self.regioinvent_database_name
                # update production exchange. Should always be the first one that comes out
                regio_process['exchanges'][0]['code'] = regio_process['code']
                regio_process['exchanges'][0]['database'] = regio_process['database']
                regio_process['exchanges'][0]['location'] = regio_process['location']
                # input the regionalized process into the global production market
                global_market_activity['exchanges'].append({"amount": exporters.loc[export_country],
                                                            "type": "technosphere",
                                                            "name": regio_process['name'],
                                                            "product": regio_process['reference product'],
                                                            "unit": regio_process['unit'],
                                                            "location": export_country,
                                                            "database": self.regioinvent_database_name,
                                                            "code": global_market_activity['code'],
                                                            "input": (regio_process['database'], regio_process['code']),
                                                            "output": (global_market_activity['database'],
                                                                       global_market_activity['code'])})
                return regio_process

            for exporter in exporters.index:
                # reset regio_process
                regio_process = None
                if exporter in available_geographies and exporter not in ['RoW']:
                    regio_process = copy_process(product, exporter, exporter)
                elif exporter in self.country_to_ecoinvent_regions and regio_process:
                    for potential_region in self.country_to_ecoinvent_regions[exporter]:
                        if potential_region in available_geographies:
                            regio_process = copy_process(product, potential_region, exporter)
                elif 'RoW' in available_geographies:
                    regio_process = copy_process(product, 'RoW', exporter)
                elif 'GLO' in available_geographies:
                    regio_process = copy_process(product, 'GLO', exporter)
                else:
                    if available_geographies:
                        # if not RoW/GLO processes, take the first available geography by default...
                        regio_process = copy_process(product, available_geographies[0], exporter)
                        self.assigned_random_geography.append([product, exporter])

                if regio_process:
                    if self.test_input_presence(regio_process, 'electricity', 'technosphere',
                                                      extra='aluminium/electricity'):
                        regio_process = self.change_aluminium_electricity(regio_process, exporter)
                    elif self.test_input_presence(regio_process, 'electricity', 'technosphere',
                                                        extra='cobalt/electricity'):
                        regio_process = self.change_cobalt_electricity(regio_process, exporter)
                    elif self.test_input_presence(regio_process, 'electricity', 'technosphere', extra='voltage'):
                        regio_process = self.change_electricity(regio_process, exporter)
                    if self.test_input_presence(regio_process, 'municipal solid waste', 'technosphere'):
                        regio_process = self.change_waste(regio_process, exporter)
                    # if self.test_input_presence(regio_process, 'Water', 'biosphere'):
                    #     regio_process = self.change_water(regio_process, exporter, exporter)
                    if self.test_input_presence(regio_process, 'heat, district or industrial, natural gas',
                                                      'technosphere'):
                        regio_process = self.change_heat(regio_process, exporter,
                                                               'heat, district or industrial, natural gas')
                    if self.test_input_presence(regio_process,
                                                      'heat, district or industrial, other than natural gas',
                                                      'technosphere'):
                        regio_process = self.change_heat(regio_process, exporter,
                                                               'heat, district or industrial, other than natural gas')
                    if self.test_input_presence(regio_process,
                                                      'heat, central or small-scale, other than natural gas',
                                                      'technosphere'):
                        regio_process = self.change_heat(regio_process, exporter,
                                                               'heat, central or small-scale, other than natural gas')

                if regio_process:
                    self.ei39_wurst.append(regio_process)
            self.ei39_wurst.append(global_market_activity)

        # save RAM
        del self.export_data

    def create_consumption_markets(self):

        self.logger.info("Creating consumption markets...")

        for product in self.eco_to_hs_class:
            cmd_consumption_data = self.consumption_data[self.consumption_data.cmdCode == self.eco_to_hs_class[product]].copy('deep')
            # calculate the average import volume for each country
            cmd_consumption_data = cmd_consumption_data.groupby(['reporterISO', 'partnerISO']).agg({'qty': 'mean'})
            # change to relative
            importers = (cmd_consumption_data.groupby(level=0).sum() / cmd_consumption_data.sum().sum()).sort_values(
                by='qty', ascending=False)
            # only keep importers till the 99% of total imports
            limit = importers.index.get_loc(importers[importers.cumsum() > 0.99].dropna().index[0]) + 1
            # aggregate the rest
            remainder = cmd_consumption_data.loc[importers.index[limit:]].groupby(level=1).sum()
            cmd_consumption_data = cmd_consumption_data.loc[importers.index[:limit]]
            # assign the aggregate to RoW
            cmd_consumption_data = pd.concat([cmd_consumption_data, pd.concat([remainder], keys=['RoW'])])
            cmd_consumption_data.index = pd.MultiIndex.from_tuples([i for i in cmd_consumption_data.index])
            cmd_consumption_data = cmd_consumption_data.sort_index()
            # for each importer, calculate the relative shares of each country it is importing from
            for importer in cmd_consumption_data.index.levels[0]:
                cmd_consumption_data.loc[importer, 'qty'] = (
                        cmd_consumption_data.loc[importer, 'qty'] / cmd_consumption_data.loc[importer, 'qty'].sum()).values
            # we need to add the aggregate to potentially already existing RoW exchanges
            cmd_consumption_data = pd.concat([cmd_consumption_data.drop('RoW'),
                                         pd.concat([cmd_consumption_data.loc['RoW'].groupby(level=0).sum()], keys=['RoW'])])
            # create the consumption markets
            for importer in cmd_consumption_data.index.levels[0]:
                new_import_data = bw2.Database(self.regioinvent_database_name).new_activity(uuid.uuid4().hex)
                new_import_data['name'] = 'consumption market for ' + product
                new_import_data['reference product'] = product
                new_import_data['location'] = importer
                new_import_data['type'] = 'process'
                new_import_data['unit'] = 'kilogram'
                new_import_data.save()
                # create the production flow
                new_exc = new_import_data.new_exchange(input=new_import_data.key, amount=1, type='production')
                new_exc.save()

                available_trading_partners = [i[0] for i in self.ecoinvent_cursor.execute(
                    f"""SELECT location FROM activitydataset WHERE product='{product}' AND 
                    database='{self.regioinvent_database_name}' AND name NOT LIKE '%consumption market%'""").fetchall()]

                for trading_partner in cmd_consumption_data.loc[importer].index:
                    if trading_partner in available_trading_partners:
                        trading_partner_key = bw2.Database(self.regioinvent_database_name).get(self.ecoinvent_cursor.execute(
                            f"""SELECT code FROM activitydataset WHERE product='{product}' AND 
                            database='{self.regioinvent_database_name}' AND name NOT LIKE '%consumption market%' AND 
                            location='{trading_partner}'""").fetchall()[0][0]).key
                        new_trade_exc = new_import_data.new_exchange(
                            input=trading_partner_key,
                            amount=cmd_consumption_data.loc[(importer, trading_partner), 'qty'],
                            type='technosphere', name=product)
                        new_trade_exc.save()
                    elif trading_partner in self.country_to_ecoinvent_regions:
                        for potential_region in self.country_to_ecoinvent_regions[trading_partner]:
                            if potential_region in available_trading_partners:
                                trading_partner_key = bw2.Database(self.regioinvent_database_name).get(
                                    self.ecoinvent_cursor.execute(
                            f"""SELECT code FROM activitydataset WHERE product='{product}' AND 
                            database='{self.regioinvent_database_name}' AND name NOT LIKE '%consumption market%' AND 
                            location='{potential_region}'""").fetchall()[0][0]).key
                        new_trade_exc = new_import_data.new_exchange(
                            input=trading_partner_key,
                            amount=cmd_consumption_data.loc[(importer, trading_partner), 'qty'],
                            type='technosphere', name=product)
                        new_trade_exc.save()
                    else:
                        trading_partner_key = bw2.Database(self.regioinvent_database_name).get(self.ecoinvent_cursor.execute(
                            f"""SELECT code FROM activitydataset WHERE product='{product}' AND 
                            database='{self.regioinvent_database_name}' AND name NOT LIKE '%consumption market%' AND 
                            location='RoW'""").fetchall()[0][0]).key
                        new_trade_exc = new_import_data.new_exchange(
                            input=trading_partner_key,
                            amount=cmd_consumption_data.loc[(importer, trading_partner), 'qty'],
                            type='technosphere', name=product)
                        new_trade_exc.save()

                # check for duplicate input codes with different values (coming from RoW)
                duplicates = [item for item, count in collections.Counter(
                    [i.input['code'] for i in new_import_data.exchanges()]).items() if count > 1]
                # aggregate duplicates in one flow
                for duplicate in duplicates:
                    total = sum([i.amount for i in new_import_data.exchanges() if i.input['code'] == duplicate])
                    name = [i.input['name'] for i in new_import_data.exchanges() if i.input['code'] == duplicate][0]
                    [i.delete() for i in new_import_data.exchanges() if i.input['code'] == duplicate]
                    new_exc = new_import_data.new_exchange(input=(self.regioinvent_database_name, duplicate),
                                                           amount=total, name=name, type='technosphere')
                    new_exc.save()

    def second_order_regionalization(self):

        self.logger.info("Performing second order regionalization...")

        for process in bw2.Database(self.regioinvent_database_name):
            for inputt in process.technosphere():
                # if it's an ecoinvent input
                if inputt.as_dict()['input'][0] == self.new_regionalized_ecoinvent_database_name:
                    # check if we regionalized the commodity
                    if inputt.as_dict()['name'] in self.eco_to_hs_class.keys():
                        exchange_amount = sum([i.amount for i in process.technosphere() if
                                               i.as_dict()['name'] == inputt.as_dict()['name']])
                        available_geographies = [i[0] for i in self.ecoinvent_cursor.execute(
                            f"""SELECT location FROM activitydataset WHERE product='{inputt.as_dict()['name']}' AND 
                            database='{self.regioinvent_database_name}' AND 
                            name LIKE '%consumption market%'""").fetchall()]
                        if process.as_dict()['location'] in available_geographies:
                            new_exchange_key = bw2.Database(self.regioinvent_database_name).get(
                                self.ecoinvent_cursor.execute(f"""SELECT code FROM activitydataset WHERE 
                                product='{inputt.as_dict()['name']}' AND 
                                database='{self.regioinvent_database_name}' AND 
                                name LIKE '%consumption market%' AND 
                                location='{process.as_dict()['location']}'""").fetchall()[0][0]).key
                        elif process.as_dict()['location'] in self.country_to_ecoinvent_regions:
                            for potential_region in self.country_to_ecoinvent_regions[process.as_dict()['location']]:
                                if potential_region in available_geographies:
                                    new_exchange_key = bw2.Database(self.regioinvent_database_name).get(
                                        self.ecoinvent_cursor.execute(f"""SELECT code FROM activitydataset WHERE 
                                product='{inputt.as_dict()['name']}' AND 
                                database='{self.regioinvent_database_name}' AND 
                                name LIKE '%consumption market%' AND 
                                location='{potential_region}'""").fetchall()[0][0]).key
                        else:
                            new_exchange_key = bw2.Database(self.regioinvent_database_name).get(
                                self.ecoinvent_cursor.execute(f"""SELECT code FROM activitydataset WHERE 
                                product='{inputt.as_dict()['name']}' AND 
                                database='{self.regioinvent_database_name}' AND 
                                name LIKE '%consumption market%' AND 
                                location='RoW'""").fetchall()[0][0]).key

                        [i.delete() for i in process.technosphere() if i.as_dict()['name'] == inputt.as_dict()['name']]
                        new_exc = process.new_exchange(input=new_exchange_key, amount=exchange_amount,
                                                       type='technosphere', name=inputt.as_dict()['name'])
                        new_exc.save()

        self.logger.info("Finished...")

    # ==================================================================================================================
    # -------------------------------------------Supporting functions---------------------------------------------------

    def change_electricity(self, process, export_country):
        # identify electricity related exchanges
        electricity_product_names = list(set([i['product'] for i in process['exchanges'] if
                                             'electricity' in i['name'] and 'aluminium' not in i['name'] and
                                              'cobalt' not in i['name'] and 'voltage' in i['name']]))
        for electricity_product_name in electricity_product_names:
            unit_name = list(set([i['unit'] for i in process['exchanges'] if 'electricity' in i['name'] and
                                  'aluminium' not in i['name'] and 'cobalt' not in i['name']]))
            assert len(unit_name) == 1
            unit_name = unit_name[0]
            qty_of_electricity = sum(
                [i['amount'] for i in process['exchanges'] if electricity_product_name == i['product']])

            # remove electricity flows from non-appropriated geography
            for exc in process['exchanges'][:]:
                if (electricity_product_name == exc['product'] and 'aluminium' not in exc['name'] and
                        'cobalt' not in exc['name'] and 'voltage' in exc['name']):
                    process['exchanges'].remove(exc)

            electricity_region = None
            if export_country in self.electricity_geos:
                electricity_region = export_country
            elif export_country != 'RoW' and export_country in self.country_to_ecoinvent_regions and not electricity_region:
                for potential_region in self.country_to_ecoinvent_regions[export_country]:
                    if potential_region in self.electricity_geos:
                        electricity_region = potential_region
            if not electricity_region:
                electricity_region = 'GLO'

            if electricity_region in ['BR', 'CA', 'CN', 'GLO', 'IN', 'RAF', 'RAS', 'RER', 'RLA', 'RME', 'RNA', 'US']:
                electricity_activity_name = 'market group for ' + electricity_product_name
            else:
                electricity_activity_name = 'market for ' + electricity_product_name

            electricity_code = ws.get_one(self.ei39_wurst, ws.equals('reference product', electricity_product_name),
                                          ws.equals('name', electricity_activity_name),
                                          ws.equals('location', electricity_region))['code']

            # create the regionalized flow for electricity
            process['exchanges'].append({"amount": qty_of_electricity,
                                           "product": electricity_product_name,
                                           "name": electricity_activity_name,
                                           "location": electricity_region,
                                           "unit": unit_name,
                                           "database": self.regioinvent_database_name,
                                           "type": "technosphere",
                                           "input": (self.ecoinvent_database_name, electricity_code),
                                           "output": (process['database'], process['code'])})

        return process

    def change_aluminium_electricity(self, process, export_country):
        # identify electricity related exchanges
        electricity_product_names = list(set([i['product'] for i in process['exchanges'] if
                                              'electricity' in i['name'] and 'aluminium' in i['name']]))
        for electricity_product_name in electricity_product_names:
            unit_name = list(set([i['unit'] for i in process['exchanges'] if
                                              'electricity' in i['name']  and 'aluminium' in i['name']]))

            assert len(unit_name) == 1
            unit_name = unit_name[0]
            qty_of_electricity = sum(
                [i['amount'] for i in process['exchanges'] if electricity_product_name == i['product']])

            # remove electricity flows from non-appropriated geography
            for exc in process['exchanges'][:]:
                if electricity_product_name == exc['product'] and 'aluminium' in exc['name']:
                    process['exchanges'].remove(exc)

            electricity_region = None
            if export_country in self.electricity_aluminium_geos:
                electricity_region = export_country
            elif export_country != 'RoW' and export_country in self.country_to_ecoinvent_regions and not electricity_region:
                for potential_region in self.country_to_ecoinvent_regions[export_country]:
                    if potential_region in self.electricity_aluminium_geos:
                        electricity_region = potential_region
            if not electricity_region:
                electricity_region = 'RoW'

            electricity_activity_name = 'market for ' + electricity_product_name

            electricity_code = ws.get_one(self.ei39_wurst, ws.equals('reference product', electricity_product_name),
                                          ws.equals('name', electricity_activity_name),
                                          ws.equals('location', electricity_region))['code']

            # create the regionalized flow for electricity
            process['exchanges'].append({"amount": qty_of_electricity,
                                           "product": electricity_product_name,
                                           "name": electricity_activity_name,
                                           "location": electricity_region,
                                           "unit": unit_name,
                                           "database": self.regioinvent_database_name,
                                           "type": "technosphere",
                                           "input": (self.ecoinvent_database_name, electricity_code),
                                           "output": (process['database'], process['code'])})

        return process

    def change_cobalt_electricity(self, process, export_country):
        # identify electricity related exchanges
        electricity_product_names = list(set([i['product'] for i in process['exchanges'] if
                                              'electricity' in i['name'] and 'cobalt' in i['name']]))
        for electricity_product_name in electricity_product_names:
            unit_name = list(set([i['unit'] for i in process['exchanges'] if 'electricity' in i['name'] and
                                  'cobalt' in i['name']]))

            assert len(unit_name) == 1
            unit_name = unit_name[0]
            qty_of_electricity = sum(
                [i['amount'] for i in process['exchanges'] if electricity_product_name == i['product']])

            # remove electricity flows from non-appropriated geography
            for exc in process['exchanges'][:]:
                if electricity_product_name == exc['product'] and 'cobalt' in exc['name']:
                    process['exchanges'].remove(exc)

            # GLO is the only geography available for electricity, cobalt industry
            electricity_region = 'GLO'

            electricity_activity_name = 'market for ' + electricity_product_name

            electricity_code = ws.get_one(self.ei39_wurst, ws.equals('reference product', electricity_product_name),
                                          ws.equals('name', electricity_activity_name),
                                          ws.equals('location', electricity_region))['code']

            # create the regionalized flow for electricity
            process['exchanges'].append({"amount": qty_of_electricity,
                                           "product": electricity_product_name,
                                           "name": electricity_activity_name,
                                           "location": electricity_region,
                                           "unit": unit_name,
                                           "database": self.regioinvent_database_name,
                                           "type": "technosphere",
                                           "input": (self.ecoinvent_database_name, electricity_code),
                                           "output": (process['database'], process['code'])})

        return process

    def change_waste(self, process, export_country):
        waste_product_name = 'municipal solid waste'
        unit_name = list(set([i['unit'] for i in process['exchanges'] if waste_product_name == i['product']]))
        # if fail -> huh. It should never fail
        assert len(unit_name) == 1
        unit_name = unit_name[0]
        qty_of_waste = sum([i['amount'] for i in process['exchanges'] if waste_product_name == i['product']])

        # remove waste flows from non-appropriated geography
        for exc in process['exchanges'][:]:
            if waste_product_name == exc['product']:
                process['exchanges'].remove(exc)

        if export_country in self.waste_geos:
            waste_region = export_country
        elif (export_country in self.country_to_ecoinvent_regions and
              self.country_to_ecoinvent_regions[export_country][0] == "RER"):
            waste_region = 'Europe without Switzerland'
        else:
            waste_region = 'RoW'

        if waste_region == 'Europe without Switzerland':
            waste_activity_name = 'market group for ' + waste_product_name
        else:
            waste_activity_name = 'market for ' + waste_product_name

        waste_code = ws.get_one(self.ei39_wurst, ws.equals('reference product', waste_product_name),
                                ws.equals('name', waste_activity_name),
                                ws.equals('location', waste_region))['code']

        # create the regionalized flow for waste
        process['exchanges'].append({"amount": qty_of_waste,
                                           "product": waste_product_name,
                                           "name": waste_activity_name,
                                           "location": waste_region,
                                           "unit": unit_name,
                                           "database": self.regioinvent_database_name,
                                           "type": "technosphere",
                                           "input": (self.ecoinvent_database_name, waste_code),
                                           "output": (process['database'], process['code'])})

        return process

    def change_water(self, process, export_country, region):
        for water_type in self.water_flows_in_ecoinvent:
            water_flow_name = [i['name'] for i in process['exchanges'] if
                               i['name'].split(', ' + region)[0] == water_type and i['categories'][0] != 'air']
            if water_flow_name:
                # check only one water flow identified
                assert len(water_flow_name) == 1
                water_flow_name = water_flow_name[0]
                unit_name = list(set([i['unit'] for i in process['exchanges'] if
                                      i['name'].split(', ' + region)[0] == water_type and i['categories'][
                                          0] != 'air']))[0]
                qty_of_water_flow = sum([i['amount'] for i in process['exchanges'] if
                                         i['name'].split(', ' + region)[0] == water_type and i['categories'][
                                             0] != 'air'])
                categories = [i['categories'] for i in process['exchanges'] if
                              i['name'].split(', ' + region)[0] == water_type and i['categories'][0] != 'air'][0]

                # remove water flows from non-appropriated geography
                for exc in process['exchanges'][:]:
                    if exc['name'].split(', ' + region)[0] == water_type and exc['categories'][0] != 'air':
                        process['exchanges'].remove(exc)

                if export_country in self.water_geos:
                    water_key = (self.name_regionalized_biosphere_database, [pickle.loads(i)['code'] for i in pd.read_sql(
                        f"""SELECT data FROM activitydataset WHERE name='{water_type + ', ' + export_country}' AND 
                            database='{self.name_regionalized_biosphere_database}'""", self.ecoinvent_conn).data if
                                                                        pickle.loads(i)['categories'] == categories][0])
                elif export_country in self.country_to_ecoinvent_regions:
                    for potential_region in self.country_to_ecoinvent_regions[export_country]:
                        if potential_region in self.water_geos:
                            water_key = (self.name_regionalized_biosphere_database, [pickle.loads(i)['code'] for i in pd.read_sql(
                        f"""SELECT data FROM activitydataset WHERE name='{
                        water_type + ', ' + potential_region}' AND 
                            database='{self.name_regionalized_biosphere_database}'""", self.ecoinvent_conn).data if
                                                                        pickle.loads(i)['categories'] == categories][0])
                else:
                    water_key = (self.name_regionalized_biosphere_database, [pickle.loads(i)['code'] for i in pd.read_sql(
                        f"""SELECT data FROM activitydataset WHERE name='{water_type + ', RoW'}' AND  
                        database='{self.name_regionalized_biosphere_database}'""", self.ecoinvent_conn).data if
                                                                        pickle.loads(i)['categories'] == categories][0])

                # create the regionalized flow for waste
                process['exchanges'].append({"amount": qty_of_water_flow,
                                                   "product": None,
                                                   "name": water_flow_name,
                                                   "location": None,
                                                   "unit": unit_name,
                                                   "database": self.name_regionalized_biosphere_database,
                                                   "categories": categories,
                                                   "input": water_key,
                                                   "type": "biosphere"})

        return process

    def change_heat(self, process, export_country, heat_flow):
        if heat_flow == 'heat, district or industrial, natural gas':
            heat_process_countries = self.heat_district_ng
        if heat_flow == 'heat, district or industrial, other than natural gas':
            heat_process_countries = self.heat_district_non_ng
        if heat_flow == 'heat, central or small-scale, other than natural gas':
            heat_process_countries = self.heat_small_scale_non_ng

        unit_name = list(set([i['unit'] for i in process['exchanges'] if heat_flow == i['product']]))
        assert len(unit_name) == 1
        unit_name = unit_name[0]
        qty_of_heat = sum(
            [i['amount'] for i in process['exchanges'] if heat_flow == i['product']])

        # remove heat flows from non-appropriated geography
        for exc in process['exchanges'][:]:
            if heat_flow == exc['product']:
                process['exchanges'].remove(exc)

        # determine qty of heat for national mix through its share of the regional mix (e.g., DE in RER market for heat)
        # CH is its own market
        if export_country == 'CH':
            region_heat = export_country
        elif (export_country in self.country_to_ecoinvent_regions and
              self.country_to_ecoinvent_regions[export_country][0] == 'RER'):
            region_heat = "Europe without Switzerland"
        else:
            region_heat = "RoW"

        # check if the country has a national production heat process, if not take the region or RoW
        if export_country not in heat_process_countries:
            if (export_country in self.country_to_ecoinvent_regions and
                    self.country_to_ecoinvent_regions[export_country][0] == 'RER'):
                export_country = "Europe without Switzerland"
            else:
                export_country = "RoW"

        # select region heat market process
        region_heat_process = ws.get_many(self.ei39_wurst,
                                          ws.equals("reference product", heat_flow),
                                          ws.equals("location", region_heat),
                                          ws.equals("database", self.ecoinvent_database_name),
                                          ws.contains("name", "market for"))

        # extracting amount of heat of country within region heat market process
        heat_exchanges = {}
        for ds in region_heat_process:
            for exc in ws.technosphere(ds, *[ws.equals("product", heat_flow),
                                             ws.equals("location", export_country)]):
                heat_exchanges[exc['name']] = exc['amount']
        # make it relative amounts
        heat_exchanges = {k: v / sum(heat_exchanges.values()) for k, v in heat_exchanges.items()}
        # scale the relative amount to the qty of heat of process
        heat_exchanges = {k: v * qty_of_heat for k, v in heat_exchanges.items()}

        # add regionalized exchange of heat
        for heat_exc in heat_exchanges.keys():
            ds = ws.get_one(self.ei39_wurst, ws.equals("name", heat_exc),
                            ws.equals("reference product", heat_flow),
                            ws.equals("location", export_country),
                            ws.equals("database", self.ecoinvent_database_name))

            process['exchanges'].append({"amount": heat_exchanges[heat_exc],
                                           "product": heat_flow,
                                           "name": heat_exc,
                                           "location": export_country,
                                           "unit": unit_name,
                                           "database": self.regioinvent_database_name,
                                           "type": "technosphere",
                                           "input": (ds['database'], ds['code']),
                                           "output": (process['database'], process['code'])})

        return process

    def test_input_presence(self, process, input_name, input_type, extra=None):
        if extra == 'aluminium/electricity':
            for exc in ws.technosphere(process, ws.contains("name", input_name), ws.contains("name", "aluminium")):
                return True
        elif extra == 'cobalt/electricity':
            for exc in ws.technosphere(process, ws.contains("name", input_name), ws.contains("name", "cobalt")):
                return True
        elif extra == 'voltage':
            for exc in ws.technosphere(process, ws.contains("name", input_name), ws.contains("name", "voltage")):
                return True
        else:
            if input_type == 'technosphere':
                for exc in ws.technosphere(process, ws.contains("name", input_name)):
                    return True
            elif input_type == 'biosphere':
                for exc in ws.biosphere(process, *[ws.contains("name", input_name),
                                                   ws.exclude(ws.equals("categories", ('air',)))]):
                    return True

    def format_export_data(self):
        self.export_data = pd.read_sql('SELECT * FROM "Export data"', con=self.trade_conn)
        # only keep total export values (no need for destination detail)
        self.export_data = self.export_data[self.export_data.partnerISO == 'W00']
        # check if AtlQty is defined whenever Qty is empty. If it is, then use that value.
        self.export_data.loc[self.export_data.qty == 0, 'qty'] = self.export_data.loc[self.export_data.qty == 0, 'altQty']
        # don't need AtlQty afterwards and drop zero values
        self.export_data = self.export_data.drop('altQty', axis=1)
        self.export_data = self.export_data.loc[self.export_data.qty != 0]
        # DEAL WITH UNITS IN TRADE DATA
        # remove mistakes in units from data reporter
        self.export_data = self.export_data.drop([i for i in self.export_data.index if (
                (self.export_data.loc[i, 'cmdCode'] == '7005' and self.export_data.loc[i, 'qtyUnitAbbr'] == 'kg') or
                (self.export_data.loc[i, 'cmdCode'] == '850720' and self.export_data.loc[i, 'qtyUnitAbbr'] in ['kg','ce/el']) or
                (self.export_data.loc[i, 'cmdCode'] == '280421' and self.export_data.loc[i, 'qtyUnitAbbr'] == 'u') or
                (self.export_data.loc[i, 'cmdCode'] == '280440' and self.export_data.loc[i, 'qtyUnitAbbr'] == 'u'))])
        # convert data in different units
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '2804' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qty'] /= 0.08375  # kg/m3 density of hydrogen
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '280421' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qty'] /= 1.784  # kg/m3 density of argon
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '280429' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qty'] /= 0.166  # kg/m3 density of helium
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '4412' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qty'] /= 700  # kg/m3 density of wood
        # change unit name
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '2804' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qtyUnitAbbr'] = 'm³'
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '280421' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qtyUnitAbbr'] = 'm³'
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '280429' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qtyUnitAbbr'] = 'm³'
        self.export_data.loc[[i for i in self.export_data.index if self.export_data.loc[i, 'cmdCode'] == '4412' and self.export_data.loc[
            i, 'qtyUnitAbbr'] == 'kg'], 'qtyUnitAbbr'] = 'm³'
        # check there are no other instances of data in multiple units
        data_in_kg = set(self.export_data.loc[self.export_data.qtyUnitAbbr == 'kg'].cmdCode)
        data_in_L = set(self.export_data.loc[self.export_data.qtyUnitAbbr == 'l'].cmdCode)
        data_in_m2 = set(self.export_data.loc[self.export_data.qtyUnitAbbr == 'm²'].cmdCode)
        data_in_m3 = set(self.export_data.loc[self.export_data.qtyUnitAbbr == 'm³'].cmdCode)
        data_in_u = set(self.export_data.loc[self.export_data.qtyUnitAbbr == 'u'].cmdCode)
        assert not (data_in_kg & data_in_L & data_in_m2 & data_in_m3 & data_in_u)

    def estimate_domestic_production(self):

        domestic_prod = pd.read_sql('SELECT * FROM "Domestic production"', con=self.trade_conn)

        self.domestic_data = self.export_data.copy('deep')
        self.domestic_data.loc[:, 'COMTRADE_reporter'] = self.domestic_data.reporterISO.copy()
        # go from ISO3 codes to country codes for respective databases
        self.export_data.reporterISO = [self.convert_ecoinvent_geos[i] for i in self.export_data.reporterISO]
        self.domestic_data.reporterISO = [self.convert_ecoinvent_geos[i] for i in self.domestic_data.reporterISO]
        self.domestic_data.COMTRADE_reporter = [self.convert_exiobase_geos[i] for i in
                                                self.domestic_data.COMTRADE_reporter]

        self.domestic_data = self.domestic_data.merge(
            pd.DataFrame.from_dict(self.hs_class_to_exio, orient='index', columns=['cmdExio']).reset_index().rename(
                columns={'index': 'cmdCode'}))
        self.domestic_data = self.domestic_data.merge(domestic_prod, left_on=['COMTRADE_reporter', 'cmdExio'],
                                                      right_on=['country', 'commodity'], how='left')
        self.domestic_data.qty = (self.domestic_data.qty / (1 - self.domestic_data.loc[:, 'domestic use (%)'] / 100) *
                                  self.domestic_data.loc[:, 'domestic use (%)'] / 100)
        self.domestic_data.partnerISO = self.domestic_data.reporterISO

    def format_import_data(self):
        self.import_data = pd.read_sql('SELECT * FROM "Import data"', con=self.trade_conn)
        # remove trade with "World"
        self.import_data = self.import_data.drop(self.import_data[self.import_data.partnerISO == 'W00'].index)
        # go from ISO3 codes to ISO2 codes
        self.import_data.reporterISO = [self.convert_ecoinvent_geos[i] for i in self.import_data.reporterISO]
        self.import_data.partnerISO = [self.convert_ecoinvent_geos[i] for i in self.import_data.partnerISO]
        # check if AtlQty is defined whenever Qty is empty. If it is, then use that value.
        self.import_data.loc[self.import_data.qty == 0, 'qty'] = self.import_data.loc[self.import_data.qty == 0, 'altQty']
        # don't need AtlQty afterwards and drop zero values
        self.import_data = self.import_data.drop('altQty', axis=1)
        self.import_data = self.import_data.loc[self.import_data.qty != 0]
        # convert data in different units
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:,
                                 'cmdCode'] == '2804'].index, 'qty'] /= 0.08375  # kg/m3 density of hydrogen
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:,
                                 'cmdCode'] == '280421'].index, 'qty'] /= 1.784  # kg/m3 density of argon
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:,
                                 'cmdCode'] == '4412'].index, 'qty'] /= 700  # kg/m3 density of wood
        # change unit name
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:, 'cmdCode'] == '2804'].index, 'qtyUnitAbbr'] = 'm³'
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:, 'cmdCode'] == '280421'].index, 'qtyUnitAbbr'] = 'm³'
        self.import_data.loc[self.import_data.loc[self.import_data.loc[:, 'qtyUnitAbbr'] == 'kg'].loc[
                                 self.import_data.loc[:, 'cmdCode'] == '4412'].index, 'qtyUnitAbbr'] = 'm³'
        # check there are no other instances of data in multiple units
        data_in_kg = set(self.import_data.loc[self.import_data.qtyUnitAbbr == 'kg'].cmdCode)
        data_in_L = set(self.import_data.loc[self.import_data.qtyUnitAbbr == 'l'].cmdCode)
        data_in_m2 = set(self.import_data.loc[self.import_data.qtyUnitAbbr == 'm²'].cmdCode)
        data_in_m3 = set(self.import_data.loc[self.import_data.qtyUnitAbbr == 'm³'].cmdCode)
        data_in_u = set(self.import_data.loc[self.import_data.qtyUnitAbbr == 'u'].cmdCode)
        assert not (data_in_kg & data_in_L & data_in_m2 & data_in_m3 & data_in_u)

        # remove artefacts of domestic trade from international trade data
        self.import_data = self.import_data.drop(
            self.import_data.loc[self.import_data.loc[:, 'reporterISO'] == self.import_data.loc[:, 'partnerISO']].index)
        # concatenate import and domestic data
        self.consumption_data = pd.concat([self.import_data, self.domestic_data.loc[:, self.import_data.columns]])
        # get rid of infinite values
        self.consumption_data.qty = self.consumption_data.qty.replace(np.inf, 0.0)

        # save RAM
        del self.import_data
        del self.domestic_data

    def write_to_database(self):
        # only select processes of the newly created regioinvent database
        regioinvent_filter = ws.get_many(self.ei39_wurst, ws.equals("database", self.regioinvent_database_name))

        regioinvent = []
        for ds in regioinvent_filter:
            regioinvent.append(ds)

        # transform into dictionary to import through brightway
        # (importing through wurst requires to change the database name or smth, not what we want)
        regioinvent_data = {(i['database'], i['code']): i for i in regioinvent}

        # recreate inputs in edges (exchanges)
        for pr in regioinvent_data:
            for exc in regioinvent_data[pr]['exchanges']:
                try:
                    exc['input']
                except KeyError:
                    exc['input'] = (exc['database'], exc['code'])
        # wurst creates empty categories for activites, this creates an issue when you try to write teh bw2 database
        for pr in regioinvent_data:
            try:
                del regioinvent_data[pr]['categories']
            except KeyError:
                pass

        bw2.Database(self.regioinvent_database_name).write(regioinvent_data)

    def remove_duplicates(self):

        unique_ei39_wurst = {i['code']: i for i in self.ei39_wurst}
        self.ei39_wurst = [v for k, v in unique_ei39_wurst.items()]


def clean_up_dataframe(df):
    # remove duplicates
    df = df.drop_duplicates()
    # fix index
    df = df.reset_index().drop('index',axis=1)
    return df
