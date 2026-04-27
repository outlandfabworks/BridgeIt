import os

application = defines.get('app', 'dist/BridgeIt.app')
appname = os.path.basename(application)
files = [application]
symlinks = {'Applications': '/Applications'}
icon_locations = {appname: (140, 120), 'Applications': (360, 120)}
background = 'builtin-arrow'
window_rect = ((100, 100), (500, 300))
icon_size = 80
