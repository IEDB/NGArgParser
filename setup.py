from setuptools import setup


setup(
  name = 'ngargparser',         
  version = '0.1.13',      
  license='MIT',
  description = 'This is a standardized parser framework for CLI tools. This class will enforce certain properties or abstract methods to be implemented to properly create an Argument Parser class for other CLI tools.',   # Give a short description about your library
  url = 'https://github.com/IEDB/NGArgParser',
  download_url = 'https://github.com/IEDB/NGArgParser/archive/refs/tags/v0.1.tar.gz',
  keywords = ['iedb', 'nextgen', 'argumentparser', 'iedb tools'],
  packages = [
      'ngargparser', 
      'ngargparser.templates',
      ],  
  package_data = {
      # Include everything inside the templates/ in the package.
      'ngargparser.templates': ['*'],
      'ngargparser.templates.example-app': ['*'],
      # Include the license file in the main package
      'ngargparser': ['license-LJI.txt'],
  },
  include_package_data = True,
  install_requires = [
      'python-dotenv>=0.19.0',
  ],
  entry_points = {
    'console_scripts': [
          'cli = ngargparser.cli:main',
      ],
  },
  classifiers = [
    # How mature is this project? Common values are
    #   3 - Alpha
    #   4 - Beta
    #   5 - Production/Stable
    'Development Status :: 3 - Alpha',

    # Define that your audience are developers
    'Intended Audience :: Developers',     
    'Topic :: Software Development :: Build Tools',

    # Type of license
    'License :: OSI Approved :: MIT License',

    #Specify which pyhton versions that you want to support
    'Programming Language :: Python :: 3.8',     
    'Programming Language :: Python :: 3.9',
    'Programming Language :: Python :: 3.10',
    'Programming Language :: Python :: 3.11',
    'Programming Language :: Python :: 3.12',
  ],
)