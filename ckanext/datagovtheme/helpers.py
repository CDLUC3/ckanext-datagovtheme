import urllib, urllib2, json, re, HTMLParser, urlparse
import os, time
import logging
import csv
import StringIO

from pylons import config, request

from ckan import plugins as p
from ckan.lib import helpers as h
from ckanext.geodatagov.plugins import RESOURCE_MAPPING

log = logging.getLogger(__name__)
ckan_tmp_path = '/var/tmp/ckan'

def render_datetime_datagov(date_str):
    try:
        value = h.render_datetime(date_str)
    except (ValueError, TypeError):
        return date_str
    return value

def get_harvest_object_formats(harvest_object_id):
    try:
        obj = p.toolkit.get_action('harvest_object_show')({}, {'id': harvest_object_id})
    except p.toolkit.ObjectNotFound:
        log.info('Harvest object not found {0}:'.format(harvest_object_id))
        return {}

    def get_extra(obj, key, default=None):
        for k, v in obj['extras'].iteritems():
            if k == key:
                return v
        return default

    def format_title(format_name):
        format_titles = {
            'iso': 'ISO-19139',
            'fgdc': 'FGDC',
            'arcgis_json': 'ArcGIS JSON'
        }
        return format_titles[format_name] if format_name in format_titles else format_name

    def format_type(format_name):
        if not format_name:
            return ''

        if format_name in ('iso', 'fgdc'):
            format_type = 'xml'
        elif format_name in ('arcgis'):
            format_type = 'json'
        else:
            format_type = ''
        return format_type

    format_name = get_extra(obj, 'format', 'iso')
    original_format_name = get_extra(obj, 'original_format')

    return {
            'object_format': format_title(format_name),
            'object_format_type': format_type(format_name),
            'original_format': format_title(original_format_name),
            'original_format_type': format_type(original_format_name),
           }

def get_dynamic_menu():
    return {}

def get_harvest_source_link(package_dict):
    harvest_source_id = h.get_pkg_dict_extra(package_dict, 'harvest_source_id', None)
    harvest_source_title = h.get_pkg_dict_extra(package_dict, 'harvest_source_title', None)

    if harvest_source_id and harvest_source_title:
       msg = p.toolkit._('Harvested from')
       url = h.url_for('harvest_read', id=harvest_source_id)
       link = '{msg} <a href="{url}">{title}</a>'.format(url=url, msg=msg, title=harvest_source_title)
       return p.toolkit.literal(link)

    return ''

def is_map_viewer_format(resource):
    viewer_url = config.get('ckanext.geodatagov.spatial_preview.url')
    viewer_formats = config.get('ckanext.geodatagov.spatial_preview.formats', 'wms kml kmz').strip().split(' ')

    return viewer_url and resource.get('url') and resource.get('format', '').lower() in viewer_formats

def get_map_viewer_params(resource, advanced=False):

    params= {
        'url': resource['url'],
        'serviceType': resource.get('format'),
    }
    if resource.get('default_srs'):
        params['srs'] = resource['default_srs']

    if advanced:
        params['mode'] == 'advanced'

    return urllib.urlencode(params)

def resource_preview_custom(resource, pkg_id):

    resource_format = resource.get('format', '').lower()


    if is_map_viewer_format(resource):
        viewer_url = config.get('ckanext.geodatagov.spatial_preview.url')

        url = '{viewer_url}?{params}'.format(
                viewer_url=viewer_url,
                params=get_map_viewer_params(resource))

        return p.toolkit.render_snippet("dataviewer/snippets/data_preview.html",
               data={'embed': False,
               'resource_url': url,
               'raw_resource_url': resource['url']})

    elif resource_format in ('web map application', 'arcgis online map') \
         and ('webmap=' in resource.get('url') or 'services=' in resource.get('url')):
        url = resource['url'].replace('viewer.html', 'embedViewer.html')

        return p.toolkit.render_snippet("dataviewer/snippets/data_preview.html",
               data={'embed': False,
               'resource_url': url,
               'raw_resource_url': resource['url']})

    return h.resource_preview(resource, pkg_id)

types = {
    'web': ('html', 'data', 'esri rest', 'gov', 'org', ''),
    'preview': ('csv', 'xls', 'txt', 'jpg', 'jpeg', 'png', 'gif'),
    # "web map application" is deprecated in favour of "arcgis online map"
    'map': ('wms', 'kml', 'kmz', 'georss', 'web map application', 'arcgis online map'),
    'plotly': ('csv', 'xls', 'excel', 'openxml', 'access', 'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/csv','text/tab-separated-values',
        'application/matlab-mattext/x-matlab', 'application/x-msaccess',
        'application/msaccess', 'application/x-hdf', 'application/x-bag'),
    'cartodb': ('csv', 'xls', 'excel', 'openxml', 'kml', 'geojson', 'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/csv', 'application/vnd.google-earth.kml+xml',
        'application/vnd.geo+json'),
    'arcgis': ('esri rest', 'wms', 'kml', 'kmz', 'application/vnd.google-earth.kml+xml', 'georss')
}

def is_type_format(type, resource):
    if resource and type in types:
        format = resource.get('format', 'data').lower()
        # TODO: convert mimetypes to formats so we dont have to do this.
        mimetype = resource.get('mimetype')
        if mimetype:
            mimetype = mimetype.lower()
        if format in types[type] or mimetype in types[type]:
            return True
    return False

def is_web_format(resource):
    return is_type_format('web', resource)

def is_preview_format(resource):
    return is_type_format('preview', resource)

def is_map_format(resource):
    return is_type_format('map', resource)

def is_plotly_format(resource):
    return is_type_format('plotly', resource)

def is_cartodb_format(resource):
    return is_type_format('cartodb', resource)

def is_arcgis_format(resource):
    return is_type_format('arcgis', resource)

def arcgis_format_query(resource):
    mimetype = resource.get('mimetype', None)
    kmlstring = re.compile('(kml|kmz)');
    if kmlstring.match(str(mimetype)):
        return 'kml'
    else:
        # wms, georss
        return mimetype

def convert_resource_format(format):
    if format: format = format.lower()
    formats = RESOURCE_MAPPING.keys()
    if format in formats:
        format = RESOURCE_MAPPING[format][1]
    else:
        format = 'Web Page'

    return format

def remove_extra_chars(str_value):
    # this will remove brackets for list and dict values.
    import ast
    new_value = None

    try:
        new_value = ast.literal_eval(str_value)
    except:
        pass

    if type(new_value) is list:
        new_value = [i.strip() for i in new_value]
        ret = ', '.join(new_value)
    elif type(new_value) is dict:
        ret = ', '.join('{0}:{1}'.format(key, val) for key, val in new_value.items())
    else:
        ret = str_value

    return ret


def schema11_key_mod(key):
    key_map = {
        'Catalog @Context': 'Metadata Context',
        'Catalog @Id': 'Metadata Catalog ID',
        'Catalog Conformsto': 'Schema Version',
        'Catalog DescribedBy': 'Data Dictionary',

        # 'Identifier': 'Unique Identifier',
        'Modified': 'Data Last Modified',
        'Accesslevel': 'Public Access Level',
        'Bureaucode' : 'Bureau Code',
        'Programcode': 'Program Code',
        'Accrualperiodicity': 'Data Update Frequency',
        'Conformsto': 'Data Standard',
        'Dataquality': 'Data Quality',
        'Describedby': 'Data Dictionary',
        'Describedbytype': 'Data Dictionary Type',
        'Issued': 'Data First Published',
        'Landingpage': 'Homepage URL',
        'Primaryitinvestmentuii': 'Primary IT Investment UII',
        'References': 'Related Documents',
        'Systemofrecords': 'System of Records',
        'Theme': 'Category',
    }

    return key_map.get(key, key)

def schema11_frequency_mod(value):
    frequency_map = {
        'R/P10Y': 'Decennial',
        'R/P4Y': 'Quadrennial',
        'R/P1Y': 'Annual',
        'R/P2M': 'Bimonthly',
        'R/P0.5M': 'Bimonthly',
        'R/P3.5D': 'Semiweekly',
        'R/P1D': 'Daily',
        'R/P2W': 'Biweekly',
        'R/P0.5W': 'Biweekly',
        'R/P6M': 'Semiannual',
        'R/P2Y': 'Biennial',
        'R/P3Y': 'Triennial',
        'R/P0.33W': 'Three times a week',
        'R/P0.33M': 'Three times a month',
        'R/PT1S': 'Continuously updated',
        'R/P1M': 'Monthly',
        'R/P3M': 'Quarterly',
        'R/P0.5M': 'Semimonthly',
        'R/P4M': 'Three times a year',
        'R/P1W': 'Weekly',
    }
    return frequency_map.get(value, value)

def convert_top_category_to_list(str_value):
    import ast
    list_value = None

    try:
        list_value = ast.literal_eval(str_value)
    except:
        pass

    if type(list_value) is not list:
        list_value = []

    return list_value

def get_bureau_info(bureau_code):
    # We don't care about building the dynamic menu dropdowns.
    return {}
    WEB_PATH = '/fanstatic/datagovtheme/images/logos/'
    LOCAL_PATH = 'fanstatic_library/images/logos/'

    # handle both '007:15', or ['007:15', '007:16']
    if isinstance(bureau_code, list):
      bureau_code = bureau_code[0]

    filepath = ckan_tmp_path + '/logos/'
    filename = filepath + 'bureau.csv'
    url = config.get('ckanext.geodatagov.bureau_csv.url', '')
    if not url:
        url = config.get('ckanext.geodatagov.bureau_csv.url_default', '')

    time_file = 0
    time_current = time.time()
    try:
        time_file = os.path.getmtime(filename)
    except OSError:
        if not os.path.exists(filepath):
            os.makedirs(filepath)

    # check to see if file is older than .5 hour
    if (time_current - time_file) < 3600/2:
        file_obj = open(filename)
        file_conent = file_obj.read()
    else:
        # it means file is old, or does not exist
        # fetch new content
        if os.path.exists(filename):
            sec_timeout = 5
        else:
            sec_timeout = 20 # longer urlopen timeout if there is no backup file.

        try:
            resource = urllib2.urlopen(url, timeout=sec_timeout)
        except:
            file_obj = open(filename)
            file_conent = file_obj.read()
            # touch the file, so that it wont keep re-trying and slow down page loading
            os.utime(filename, None)
        else:
            file_obj = open(filename, 'w+')
            file_conent = resource.read()
            file_obj.write(file_conent)

    file_obj.close()

    bureau_info = {
        'code': bureau_code
    }

    try:
        agency, bureau = bureau_code.split(':')
    except ValueError:
        return None

    for row in csv.reader(StringIO.StringIO(file_conent)):
        if agency == row[2].zfill(3) \
                and bureau == row[3].zfill(2):
            bureau_info['title'] = row[1]
            bureau_info['url'] = '/dataset?q=bureauCode:"' + bureau_code + '"'
            break
    else:
        return None

    # check logo image file exists or not
    for ext in ['png', 'gif', 'jpg']:
        logo = agency + '-' + bureau + '.' + ext
        if os.path.isfile(os.path.join(os.path.dirname(__file__), LOCAL_PATH) + logo):
            bureau_info['logo'] = WEB_PATH + logo
            break
    else:
        bureau_info['logo'] = None

    return bureau_info
