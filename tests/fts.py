import dmm.utils.fts as fts
import logging

logging.basicConfig(level=logging.DEBUG)

class A:
    def __init__(self, dst_url=None, src_url=None):
        self.dst_url = dst_url
        self.src_url = src_url

test = A("xrootd-sense-ucsd-redirector-112.sdsc.optiputer.net:1094","redir-11.t2-sense.ultralight.org:1094")

print(fts.modify_link_config(test, 1000, 1000))
print(fts.modify_se_config(test, 1000, 1000))
