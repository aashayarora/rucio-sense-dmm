import configparser as ConfigParser
import os
import logging

__CONFIG = None

class Config:
    def __init__(self) -> None:
        """
        Initialize the configuration object.
        """
        self.parser = ConfigParser.ConfigParser(delimiters=('='))
        
        # Check if the configuration file path is set in the environment variable, if not, use the default configuration file path
        if "DMM_CONFIG" in os.environ:
            logging.debug("reading config defined in env")
            self.configfile = os.environ["DMM_CONFIG"]
        else:
            logging.debug("config env variable not found, reading from default path /opt/dmm/dmm.cfg")
            confdir = "/opt/dmm"
            config = os.path.join(confdir, "dmm.cfg")
            self.configfile = config if os.path.exists(config) else None

        if not self.configfile:
            raise RuntimeError("configuration file not found.")
        
        if not self.parser.read(self.configfile) == [self.configfile]:
            raise RuntimeError("could not load DMM configuration file.")

def get_config() -> ConfigParser.ConfigParser:
    """
    Get the configuration object.
    """
    global __CONFIG
    if __CONFIG is None:
        __CONFIG = Config()
    return __CONFIG.parser

def config_get(section, option, default=None, extract_function=ConfigParser.ConfigParser.get) -> str:
    """
    Get a string from the configuration file.
    """
    global __CONFIG
    logging.debug(f"Getting config option {option} from section {section}")
    try:
        return extract_function(get_config(), section, option)
    except ConfigParser.NoOptionError:
        if default is not None:
            return default
        else:
            logging.error(f"No option '{option}' in section '{section}'")
            raise

def config_get_bool(section, option, default=None) -> bool:
    """
    Get a boolean from the configuration file.
    """
    try:
        return bool(config_get(section, option, extract_function=ConfigParser.ConfigParser.getboolean))
    except:
        if default is not None:
            return default
        else:
            logging.error(f"Cannot convert option '{option}' in section '{section}' to boolean")
            raise

def config_get_int(section, option, default=None, constraint=None) -> int:
    """
    Get an integer from the configuration file.
    """
    try:
        value = int(config_get(section, option, extract_function=ConfigParser.ConfigParser.getint))
        if constraint:
            if constraint == "pos" and value <= 0:
                raise ValueError(f"Value {value} for option '{option}' in section '{section}' does not satisfy constraint '{constraint}'")
        return value
    except:
        if default is not None:
            return default
        else:
            logging.error(f"Cannot convert option '{option}' in section '{section}' to integer")
            raise