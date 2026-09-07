gnome.compile_resources('housekeeper', 'housekeeper.gresource.xml',
                        source_dir: meson.current_source_dir(), gresource_bundle: true,
                        install: true, install_dir: app_dir)
configure_file(input: app_id + '.gschema.xml', output: app_id + '.gschema.xml', copy: true)
gnome.compile_schemas(build_by_default: true)
install_data(app_id + '.gschema.xml', install_dir: get_option('datadir') / 'glib-2.0/schemas')
install_data(app_id + '.desktop', install_dir: get_option('datadir') / 'applications')
install_data(app_id + '.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
install_data('icons/hicolor/scalable/apps/' + app_id + '.svg',
             install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps')
install_data('icons/hicolor/symbolic/apps/' + app_id + '-symbolic.svg',
             install_dir: get_option('datadir') / 'icons/hicolor/symbolic/apps')
