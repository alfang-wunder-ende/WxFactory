from configparser import ConfigParser

class Configuration:
   def __init__(self, cfg_file):

      parser = ConfigParser()
      parser.read(cfg_file)

      print('\nLoading config: ' + cfg_file)
      print(parser._sections)
      print(' ')

      self.case_number      = parser.getint('Test_case', 'case_number')
      self.Williamson_angle = parser.getfloat('Test_case', 'Williamson_angle')

      self.dt               = parser.getfloat('Time_integration', 'dt')
      self.t_end            = parser.getint('Time_integration', 't_end')
      self.time_integrator  = parser.get('Time_integration', 'time_integrator')
      self.krylov_size      = parser.getint('Time_integration', 'krylov_size')
      self.tolerance        = parser.getfloat('Time_integration', 'tolerance')

      self.α                = parser.getfloat('Spatial_discretization', 'α')
      self.nbsolpts           = parser.getint('Spatial_discretization', 'nbsolpts')
      self.nb_elements      = parser.getint('Spatial_discretization', 'nb_elements')

      self.stat_freq        = parser.getint('Plot_options', 'stat_freq')
      self.plot_freq        = parser.getint('Plot_options', 'plot_freq')
      self.plot_error       = parser.getboolean('Plot_options', 'plot_error')