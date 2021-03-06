import collections
import glob
import os

from app.adjustment import Adjustment, AdjustmentDescription, AdjustmentSchedule, UserHierarchyNode, \
    CustomerHierarchyNode, LocationHierarchyNode, ProductHierarchyNode, AdjustmentParameters, LocationBusiness, \
    ItemPrice

class ExportController(object):

    def __init__(self, property_file='/Users/jaska/Work/JDA_Guess/test/Guess.properties'):
        self._current_adjustment_oid = None
        self.adjustments = {}
        self._style_to_variant_map = None
        self._item_info_file = None
        self._store_info_file = None
        self.item_price_index = 0 # how many entries in item price list
        self.item_price_map = {} # store location|color variant -> index in item price list
        self.filter_counter = 0
        self.logger = None

        self.setup(property_file)

    def setup(self, property_file):
        import ConfigParser
        cfg = ConfigParser.ConfigParser()
        cfg.read(property_file)

        self.basedir = cfg.get("MMS", "input_dir")
        self.output_dir = cfg.get("MMS", "output_dir")

        import logging

        logging.basicConfig(level=eval("logging.%s" % cfg.get("MMS", "log_level")))
        fh = logging.FileHandler(cfg.get("MMS", "log_file"))
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)

        self.logger = logging.getLogger("controller")
        self.logger.addHandler(fh)
        self.logger.info("Reading configuration from %s" % property_file)

    @property
    def current_adjustment(self):
        a = self.adjustments.get(self._current_adjustment_oid)
        if a:
            return a
        else:
            raise Exception("Current adjustment oid not set -> cannot find current adjustment!")

    @property
    def item_info_file(self):
        if self._item_info_file:  # for unittests you can assign this yourself
            return self._item_info_file

        try:
            item_file = max(glob.iglob(os.path.join(self.basedir, 'JDA_Item*.txt')), key=os.path.getctime)
        except ValueError:
            message = "Cannot find any item information files from %s" % self.basedir
            self.logger.error(message)
            raise SystemExit(message)

        return item_file

    @property
    def store_info_file(self):
        if self._store_info_file:  # for unittests you can assign this yourself
            return self._store_info_file

        try:
            store_info_file = max(glob.iglob(os.path.join(self.basedir, 'JDA_Store*.txt')), key=os.path.getctime)
        except ValueError:
            message = "Cannot find any store information files from %s" % self.basedir
            self.logger.error(message)
            raise SystemExit(message)

        return store_info_file

    @property
    def style_to_variant_map(self):
        if not self._style_to_variant_map:
            self.logger.info("Loading item information from file: %s" % self.item_info_file)

            ItemInfo = collections.namedtuple('ItemInfo',
                                              ['variant_code', 'description', 'a', 'style_code', 'c', 'd', 'e', 'f',
                                               'color',
                                               'size', 'g', 'filter_code', 'h'])
            self._style_to_variant_map = collections.defaultdict(dict)
            with open(self.item_info_file, 'r') as f:
                for ii in map(ItemInfo._make, [line.split('|') for line in f]):
                    if ii.filter_code <> '0': # filter out unwanted items
                        self.filter_counter += 1
                        continue
                    self._style_to_variant_map[ii.style_code][ii.variant_code] = ii.color

            self.logger.info("Completed loading item information. Filtered %d items." % self.filter_counter)
        return self._style_to_variant_map

    DATA_TYPES = {
        "A" : "add_adjustment",
        "D" : "add_description",
        "S" : "add_schedule",
        "U" : "add_user_hierarchy_node",
        "C" : "add_customer_hierarchy_node",
        "L" : "add_location_hierarchy_node",
        "P" : "add_product_hierarchy_node",
        "V" : "add_parameters",
        # "CB" : "customer business",
        "LB" : "add_location_business",
        "I" : "add_item_price"
    }

    def process_line(self, line):
        fields = line.split("|")
        field_type = fields[0]
        type = self.DATA_TYPES.get(field_type)
        if type:
            exec "self.%s(%s)" % (type, fields[1:])
        else:
            raise Exception("Invalid data type on line: %s" % line)

    def add_adjustment(self, fields):
        oid, external_id, description, event, rule_name = fields
        self._current_adjustment_oid = oid
        a = Adjustment(*fields)
        a.basedir = self.output_dir
        self.adjustments[oid] = a

    def add_description(self, fields):
        language_id = fields[0]
        self.current_adjustment.descriptions[language_id] = AdjustmentDescription(*fields)

    def add_schedule(self, fields):
        self.current_adjustment.schedule = AdjustmentSchedule(*fields)

    def add_user_hierarchy_node(self, fields):
        self.current_adjustment.hierarchy["U"].append(UserHierarchyNode(*fields))

    def add_customer_hierarchy_node(self, fields):
        self.current_adjustment.hierarchy["C"].append(CustomerHierarchyNode(*fields))

    def add_location_hierarchy_node(self, fields):
        self.current_adjustment.hierarchy["L"].append(LocationHierarchyNode(*fields))

    def add_product_hierarchy_node(self, fields):
        self.current_adjustment.hierarchy["P"].append(ProductHierarchyNode(*fields))

    def add_parameters(self, fields):
        parameter_name = fields[0]
        self.current_adjustment.parameters[parameter_name] =(AdjustmentParameters(*fields))

    def add_location_business(self, fields):
        location_id = fields[0]
        if not location_id in self.current_adjustment.location_business:
            self.current_adjustment.location_business[location_id] = LocationBusiness(*fields)

        if not self.current_adjustment.zone_sets:
            self.update_adjustment_zones()

    def add_item_price(self, fields):
        a = self.current_adjustment

        location_id = fields[7]
        if not location_id: # this price uses zone instead of store
            location_id = fields[6]
            fields[7] = location_id
        elif not location_id in a.location_business.keys() + list(a.zone_sets):
            raise Exception("Location business %s not found for item price: %s" % (location_id, fields))

        style_code = fields[11]
        variant_code = fields[13]
        price = fields[14]
        codes = self.get_color_codes_for_style(style_code)

        variant_key = "%s|%s" % (location_id, variant_code)

        if variant_code == '':  # style item
            self.logger.debug("%s: style item with %d colors" % (style_code, len(codes)))
            for variant_code, variant_color in codes.items():
                color_fields = fields
                color_fields[12] = variant_color
                color_fields[13] = variant_code
                variant_key = "%s|%s" % (location_id, variant_code)
                self.current_adjustment.item_price.append(ItemPrice(*color_fields))
                self.item_price_map[variant_key] = self.item_price_index
                self.item_price_index += 1
        else:
            self.logger.debug("%s is a variant item -> override style price with %s" % (variant_code, price))
            try:
                fields[12] = codes[variant_code] # get color from item info
                self.current_adjustment.item_price[self.item_price_map[variant_key]] = ItemPrice(*fields)
            except KeyError:
                self.logger.debug("Did not find color variant %s from style map (style: %s)" % (variant_code, style_code))

    def process_file(self, file_name):
        self.logger.info("Reading adjustment publish file: %s" % file_name)
        with open(file_name, 'r') as f:
            [self.process_line(line.rstrip()) for line in f ]

        [e.export_tab_delimited() for e in self.current_adjustment.get_pricing_events()]

    def get_color_codes_for_style(self, style_item_code):
        return self.style_to_variant_map[style_item_code]  # TODO: check for KeyError

    def update_adjustment_zones(self):
        self.logger.info("Loading store information from %s" % self.store_info_file)
        StoreInfo = collections.namedtuple('StoreInfo', ['store_code', 'a', 'b', 'c', 'd', 'e', 'f',
                                           'zone_code', 'g', 'h', 'i'])

        d = collections.defaultdict(set)
        with open(self.store_info_file, 'r') as f:
            for si in map(StoreInfo._make, [line.split('|') for line in f]):
                d[si.zone_code].add(si.store_code)
        self.current_adjustment.zone_sets = d
        self.logger.info("Loaded %d zones" % len(d))
        for k,v in sorted(d.items()):
            self.logger.debug("Zone %s has %d stores" % (k, len(v)))

        return self._style_to_variant_map


if __name__ == '__main__':
    import sys, string
    args = sys.argv
    print len(args), args

    if len(args) <> 3:
        raise SystemExit("Usage: %s <property file> <export_file>" % args[0])

    property_file = args[1]
    export_file = args[2]

    try:
        c = ExportController(property_file)
        c.process_file(export_file)
    except:
        import sys
        c.logger.error("Error in processing publish file %s: %s" % (export_file, sys.exc_value))
        sys.exit(1)