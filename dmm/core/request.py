import dmm.core.sense_api as sense_api

def register(self):
    """Register new request at the source and destination sites

    Note: cannot be run in parallel with another Request.register() because it would 
            incur a race condition; both instances need to modify a list at their 
            source/destination sites, so we specifically get a race condition if they 
            share either of the same endpoints.
    """
    self.src_site.add_request(self.dst_site.rse_name, self.priority)
    self.dst_site.add_request(self.src_site.rse_name, self.priority)
    if self.best_effort:
        self.src_ipv6 = self.src_site.default_ipv6
        self.dst_ipv6 = self.dst_site.default_ipv6
    else:
        self.src_ipv6 = self.src_site.reserve_ipv6()
        self.dst_ipv6 = self.dst_site.reserve_ipv6()

def deregister(self):
    """Deregister new request at the source and destination sites

    Note: cannot be run in parallel with another Request.deregister() because it would 
            incur a race condition; both instances need to modify a list at their 
            source/destination sites, so we specifically get a race condition if they 
            share either of the same endpoints.
    """
    self.src_site.remove_request(self.dst_site.rse_name, self.priority)
    self.dst_site.remove_request(self.src_site.rse_name, self.priority)
    if not self.best_effort:
        self.src_site.free_ipv6(self.src_ipv6)
        self.dst_site.free_ipv6(self.dst_ipv6)
    self.src_ipv6 = ""
    self.dst_ipv6 = ""

def get_max_bandwidth(self):
    if self.best_effort:
        return 0
    else:
        return min(
            self.src_site.get_uplink_provision(self.dst_site.rse_name),
            self.dst_site.get_uplink_provision(self.src_site.rse_name),
            self.theoretical_bandwidth
        )

def get_bandwidth_fraction(self):
    """Return bandwidth fraction

                                    my priority
    fraction = ----------------------------------------------
                sum(all priorities between my source and dest)
    """
    if self.best_effort:
        return 0
    else:
        return self.priority/self.src_site.prio_sums.get(self.dst_site.rse_name)

def reprovision_link(self):
    """Reprovision SENSE link

    Note: can be run in parallel, only modifies itself
    """
    old_bandwidth = self.bandwidth
    new_bandwidth = int(self.get_max_bandwidth()*self.get_bandwidth_fraction())
    if not self.best_effort and new_bandwidth != old_bandwidth:
        # Update SENSE link; note: in the future, this should not change the link ID
        self.sense_link_id = sense_api.reprovision_link(
            self.sense_link_id, 
            self.src_site.sense_name,
            self.dst_site.sense_name,
            self.src_ipv6,
            self.dst_ipv6,
            new_bandwidth,
            alias=self.request_id
        )
        self.bandwidth = new_bandwidth

def open_link(self):
    """Create SENSE link

    Note: can be run in parallel, only modifies itself
    """
    if not self.best_effort:
        # Initialize SENSE link and get theoretical bandwidth
        self.sense_link_id, self.theoretical_bandwidth = sense_api.stage_link(
            self.src_site.sense_name,
            self.dst_site.sense_name,
            self.src_ipv6,
            self.dst_ipv6,
            alias=self.request_id
        )
        # Get bandwidth provisioning
        self.bandwidth = int(self.get_max_bandwidth()*self.get_bandwidth_fraction())
        # Provision link
        sense_api.provision_link(
            self.sense_link_id, 
            self.src_site.sense_name,
            self.dst_site.sense_name,
            self.src_ipv6,
            self.dst_ipv6,
            self.bandwidth,
            alias=self.request_id
        )

    self.link_is_open = True

def close_link(self):
    """Close SENSE link

    Note: can be run in parallel, only modifies itself
    """
    if not self.best_effort:
        sense_api.delete_link(self.sense_link_id)
        self.sense_link_id = ""
        self.theoretical_bandwidth = -1

    self.link_is_open = False