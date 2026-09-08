import configparser as ConfigParser
import os
import logging
import threading

__CONFIG = None
__CONFIG_LOCK = threading.Lock()

# Sentinel object that distinguishes "caller explicitly passed default=None"
# from "caller did not provide a default at all".
# Using None for both meanings makes it impossible to return None as a fallback value,
# e.g. config_get_int("fts-streams", "SiteA-SiteB", default=None) would raise instead
# of returning None when the key is absent.
_UNSET = object()

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
        with __CONFIG_LOCK:
            if __CONFIG is None:  # double-checked locking
                __CONFIG = Config()
    return __CONFIG.parser

def config_get(section, option, default=_UNSET, extract_function=ConfigParser.ConfigParser.get) -> str:
    """
    Get a string from the configuration file.

    Pass ``default=None`` to return ``None`` when the key is absent (valid fallback).
    Omit ``default`` entirely to raise an exception when the key is absent.
    """
    global __CONFIG
    logging.debug(f"Getting config option {option} from section {section}")
    try:
        return extract_function(get_config(), section, option)
    except ConfigParser.NoSectionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No section '{section}' found, and no default provided")
            raise
    except ConfigParser.NoOptionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No option '{option}' found in section '{section}', and no default provided")
            raise

def config_get_bool(section, option, default=_UNSET) -> bool:
    """
    Get a boolean from the configuration file.

    Pass ``default=None`` to return ``None`` when the key is absent (valid fallback).
    Omit ``default`` entirely to raise an exception when the key is absent.
    """
    try:
        return config_get(section, option, extract_function=ConfigParser.ConfigParser.getboolean)
    except ConfigParser.NoSectionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No section '{section}' found, and no default provided")
            raise
    except ConfigParser.NoOptionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No '{option}' in section '{section}', and no default provided")
            raise
    except ValueError as ve:
        logging.error(str(ve))
        raise

def config_get_int(section, option, default=_UNSET, constraint=None) -> int:
    """
    Get an integer from the configuration file.

    Pass ``default=None`` to return ``None`` when the key is absent (valid fallback).
    Omit ``default`` entirely to raise an exception when the key is absent.
    """
    try:
        value = config_get(section, option, extract_function=ConfigParser.ConfigParser.getint)
        if constraint:
            if constraint == "pos" and value <= 0:
                raise ValueError(f"Value {value} for option '{option}' in section '{section}' does not satisfy constraint 'positive'")
            elif constraint == "nonneg" and value < 0:
                raise ValueError(f"Value {value} for option '{option}' in section '{section}' does not satisfy constraint 'non-negative'")
        return value
    except ConfigParser.NoSectionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No section '{section}' found, and no default provided")
            raise
    except ConfigParser.NoOptionError:
        if default is not _UNSET:
            return default
        else:
            logging.error(f"No '{option}' in section '{section}', and no default provided")
            raise
    except ValueError as ve:
        logging.error(str(ve))
        raise