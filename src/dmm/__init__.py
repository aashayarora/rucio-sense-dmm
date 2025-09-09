import logging
import sys

logging.basicConfig(
    format="(%(threadName)s) [%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S %p",
    level=logging.DEBUG,
    handlers=[logging.FileHandler(filename="dmm.log"), logging.StreamHandler(sys.stdout)]
)
