import dmm.utils.fts as fts
import logging

logging.basicConfig(level=logging.DEBUG)

class A:
    def __init__(self, dst_url=None, src_url=None):
        self.dst_url = dst_url
        self.src_url = src_url

test = A(src_url="cmssense4-origin-2842-1.fnal.gov:2880",dst_url="xrootd-sense-ucsd-redirector-111.sdsc.optiputer.net:1094")

print(fts.modify_link_config(test, 10, 10))
print(fts.modify_se_config(test, 10, 10))
