gnome.compile_resources('housekeeper', 'housekeeper.gresource.xml',
                        source_dir: meson.current_source_dir(), gresource_bundle: true,
                        install: true, install_dir: app_dir)
configure_file(input: app_id + '.gschema.xml', output: app_id + '.gschema.xml', copy: true)
gnome.compile_schemas(build_by_default: true, depend_files: files(app_id + '.gschema.xml'))
install_data(app_id + '.gschema.xml', install_dir: get_option('datadir') / 'glib-2.0/schemas')
custom_target('desktop-file', input: app_id + '.desktop', output: app_id + '.desktop',
              command: [python, files('../build-aux/desktop_mime.py'), '@INPUT@', '@OUTPUT@',
                        get_option('package_format')],
              install: true, install_dir: get_option('datadir') / 'applications')
install_data(app_id + '.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('icons/hicolor/scalable/apps/' + app_id + '.svg',
             install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')
install_data('icons/hicolor/symbolic/apps/' + app_id + '-symbolic.svg',
             install_dir: get_option('datadir') / 'icons/hicolor/symbolic/apps')
install_data('icons/README.md', 'icons/adwaita/COPYING',
             'icons/adwaita/COPYING_LGPL', 'icons/adwaita/COPYING_GPL',
             install_dir: app_dir / 'icon-licenses')
