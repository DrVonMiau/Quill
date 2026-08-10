application_id = 'io.github.drvonmiau.Quill'

# App icon: the final artwork (teal tile with a gold quill-in-inkpot) as a
# 512x512 PNG in the hicolor theme.
icon_dir = 'hicolor' / '512x512' / 'apps'
install_data(
  icon_dir / ('@0@.png').format(application_id),
  install_dir: get_option('datadir') / 'icons' / icon_dir
)

symbolic_dir = 'hicolor' / 'symbolic' / 'apps'
install_data(
  symbolic_dir / ('@0@-symbolic.svg').format(application_id),
  install_dir: get_option('datadir') / 'icons' / symbolic_dir
)
