#!/usr/bin/env python
import argparse
import sys
import signal
import logging

from dmm.main.dmm import DMM

def sigint_handler(dmm):
    def actual_handler(sig, frame):
        logging.info("Stopping DMM (received SIGINT)")
        sys.exit(0)
    return actual_handler

def main():
    cli = argparse.ArgumentParser(description="Rucio-SENSE data movement manager")
    cli.add_argument(
        "--loglevel", type=str, default="INFO", 
        help="log level: DEBUG, INFO, WARNING (default), or ERROR"
    )
    cli.add_argument(
        "--logfile", type=str, default="dmm.log", 
        help="path to log file (default: ./dmm.log)"
    )
    args = cli.parse_args()

    # Set up logging handlers
    handlers = [logging.FileHandler(filename=args.logfile)]
    if args.loglevel.upper() == "DEBUG":
        handlers.append(logging.StreamHandler(sys.stdout))
    # Configure logging
    logging.basicConfig(
        format="(%(threadName)s) [%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%m-%d-%Y %H:%M:%S %p",
        level=getattr(logging, args.loglevel.upper()),
        handlers=handlers
    )

    # Start DMM
    logging.info("Starting DMM")
    dmm = DMM()
    signal.signal(signal.SIGINT, sigint_handler(dmm))
    dmm.start()