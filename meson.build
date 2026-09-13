project('housekeeper', version: '0.1.14', meson_version: '>= 0.63.0', license: 'MIT')

gnome = import('gnome')
i18n = import('i18n')
python = import('python').find_installation('/usr/bin/python3', required: true)
runtime_check = run_command(python, '-c', '''
import sys, gi
assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)
''', check: true)
app_id = 'io.github.yuelinxin.housekeeper'
app_dir = get_option('prefix') / get_option('datadir') / 'housekeeper'

configuration = configuration_data()
configuration.set('PACKAGE_DIR', app_dir)
configuration.set('RESOURCE_FILE', app_dir / 'housekeeper.gresource')
configuration.set('LOCALE_DIR', get_option('prefix') / get_option('localedir'))
configuration.set('SOURCE_DIR', meson.project_source_root() / 'src')
configuration.set('BUILD_DIR', meson.project_build_root())
configuration.set('MESON_COMMAND', find_program('meson').full_path())
configuration.set('ICON_DIR', meson.project_source_root() / 'data/icons')

configure_file(input: 'build-aux/housekeeper.in', output: 'housekeeper',
               configuration: configuration, install: true,
               install_dir: get_option('bindir'), install_mode: 'rwxr-xr-x')
configure_file(input: 'build-aux/housekeeper-dev.in', output: 'housekeeper-dev',
               configuration: configuration, install: false, install_mode: 'rwxr-xr-x')
install_subdir('src/housekeeper', install_dir: app_dir,
               exclude_directories: ['__pycache__', 'providers/__pycache__', 'ui/__pycache__'])
subdir('data')
subdir('po')

pytest = find_program('/usr/bin/pytest', required: false)
if pytest.found()
  test('unit', pytest, args: ['-q', meson.project_source_root() / 'tests'],
       env: ['PYTHONPATH=' + meson.project_source_root() / 'src'], timeout: 120)
endif
gnome.post_install(glib_compile_schemas: true, gtk_update_icon_cache: true,
                   update_desktop_database: true)
