# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2020 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     Valerio Cosentino <valcos@bitergia.com>
#     Santiago Dueñas <sduenas@bitergia.com>
#

import logging
import time

import requests
import urllib3.util

from .errors import RateLimitError
from ._version import __version__

logger = logging.getLogger(__name__)


class HttpClient:
    """Abstract class for HTTP clients.

    Base class to query data sources taking care of retrying
    requests in case connection issues. If the data source
    does not send back a response after retrying a request,
    a RetryError exception is thrown.

    Sub-classes can use the methods fetch to obtain data
    from the data source.

    To track which version of the client was used during
    the fetching process, this class provides a `version`
    attribute that each client may override.

    :param base_url: base URL of the data source
    :param max_retries: number of max retries to a data source
        before raising a RetryError exception
    :param sleep_time: time (in seconds) to sleep in case
        of connection problems
    :param extra_headers: extra headers to be included in
        the requests
    :param extra_status_forcelist: a set of HTTP status codes that will
        force a retry on
    :param extra_retry_after_status: a set of HTTP status codes that will
        perform a retry respecting the Retry-After header
    :param archive: archive to store/retrieve items
    :param from_archive: if `True` the data is fetched
        from an archive
    :param ssl_verify: enable/disable SSL verification
    """
    version = '0.3.0'

    DEFAULT_SLEEP_TIME = 1

    MAX_RETRIES = 5
    MAX_RETRIES_ON_CONNECT = 5
    MAX_RETRIES_ON_READ = 5
    MAX_RETRIES_ON_REDIRECT = 5
    MAX_RETRIES_ON_STATUS = 5

    DEFAULT_METHOD_WHITELIST = False
    DEFAULT_RAISE_ON_REDIRECT = True
    DEFAULT_RAISE_ON_STATUS = True
    DEFAULT_RESPECT_RETRY_AFTER_HEADER = True

    DEFAULT_RETRY_AFTER_STATUS_CODES = [413, 429, 503]
    DEFAULT_STATUS_FORCE_LIST = [408, 423, 504]

    DEFAULT_HEADERS = {'User-Agent': 'Perceval/' + __version__}

    GET = "GET"
    POST = "POST"

    def __init__(self, base_url, max_retries=MAX_RETRIES, sleep_time=DEFAULT_SLEEP_TIME,
                 extra_headers=None, extra_status_forcelist=None, extra_retry_after_status=None,
                 archive=None, from_archive=False, ssl_verify=True):

        self.base_url = base_url
        self.ssl_verify = ssl_verify

        self.headers = dict(self.DEFAULT_HEADERS)
        if extra_headers:
            self.headers.update(extra_headers)

        self.status_forcelist = list(self.DEFAULT_STATUS_FORCE_LIST)
        if extra_status_forcelist:
            self.status_forcelist.extend(extra_status_forcelist)

        self.retry_after_status = list(self.DEFAULT_RETRY_AFTER_STATUS_CODES)
        if extra_retry_after_status:
            self.retry_after_status.extend(extra_retry_after_status)

        self.max_retries = max_retries
        self.max_retries_on_connect = self.MAX_RETRIES_ON_CONNECT
        self.max_retries_on_read = self.MAX_RETRIES_ON_READ
        self.max_retries_on_redirect = self.MAX_RETRIES_ON_REDIRECT
        self.max_retries_on_status = self.MAX_RETRIES_ON_STATUS

        self.method_whitelist = self.DEFAULT_METHOD_WHITELIST
        self.raise_on_redirect = self.DEFAULT_RAISE_ON_REDIRECT
        self.raise_on_status = self.DEFAULT_RAISE_ON_STATUS
        self.respect_retry_after_header = self.DEFAULT_RESPECT_RETRY_AFTER_HEADER
        self.sleep_time = sleep_time

        self.archive = archive
        self.from_archive = from_archive

        self._create_http_session()

    def __del__(self):
        self._close_http_session()

    def fetch(self, url, payload=None, headers=None, method=GET, stream=False, auth=None):
        """Fetch the data from a given URL.

        :param url: link to the resource
        :param payload: payload of the request
        :param headers: headers of the request
        :param method: type of request call (GET or POST)
        :param stream: defer downloading the response body until the response content is available
        :param auth: auth of the request

        :returns a response object
        """
        if self.from_archive:
            response = self._fetch_from_archive(url, payload, headers)
        else:
            response = self._fetch_from_remote(url, payload, headers, method, stream, auth)

        return response

    @staticmethod
    def sanitize_for_archive(url, headers, payload):
        """Sanitize the URL, headers and payload of a HTTP request before storing/retrieving items.
        By default, this method does not modify url, headers and payload. The modifications take
        place within the specific backends that redefine the sanitize_for_archive.

        :param: url: HTTP url request
        :param: headers: HTTP headers request
        :param: payload: HTTP payload request

        :returns url, headers and payload sanitized
        """
        return url, headers, payload

    def _fetch_from_archive(self, url, payload, headers):

        url, headers, payload = self.sanitize_for_archive(url, headers, payload)
        response = self.archive.retrieve(url, payload, headers)

        if not isinstance(response, requests.Response):
            raise response

        return response

    def _fetch_from_remote(self, url, payload, headers, method, stream, auth):

        if method == self.GET:
            response = self.session.get(url, params=payload, headers=headers, stream=stream,
                                        verify=self.ssl_verify, auth=auth)
        else:
            response = self.session.post(url, data=payload, headers=headers, stream=stream,
                                         verify=self.ssl_verify, auth=auth)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if self.archive:
                url, headers, payload = self.sanitize_for_archive(url, headers, payload)
                self.archive.store(url, payload, headers, e)
            logger.error("HTTPError: " + e.response.text)
            raise e

        if self.archive:
            url, headers, payload = self.sanitize_for_archive(url, headers, payload)
            self.archive.store(url, payload, headers, response)
        return response

    def _create_http_session(self):
        """Create a http session and initialize the retry object."""

        self.session = requests.Session()

        if self.headers:
            self.session.headers.update(self.headers)

        retries = urllib3.util.Retry(total=self.max_retries,
                                     connect=self.max_retries_on_connect,
                                     read=self.max_retries_on_read,
                                     redirect=self.max_retries_on_redirect,
                                     status=self.max_retries_on_status,
                                     allowed_methods=self.method_whitelist,
                                     status_forcelist=self.status_forcelist,
                                     backoff_factor=self.sleep_time,
                                     raise_on_redirect=self.raise_on_redirect,
                                     raise_on_status=self.raise_on_status,
                                     respect_retry_after_header=self.respect_retry_after_header)

        self.session.mount('http://', requests.adapters.HTTPAdapter(max_retries=retries))
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=retries))

    def _close_http_session(self):
        """Close the http session."""

        if self.session:
            self.session.keep_alive = False


class RateLimitHandler:
    """Class to handle rate limit for HTTP clients.

    :param sleep_for_rate: sleep until rate limit is reset
    :param min_rate_to_sleep: minimun rate needed to sleep until it will be rese
    :param rate_limit_header: header to know the current rate limit
    :param rate_limit_reset_header: header to know the next rate limit reset
    """
    version = '0.2'

    MIN_RATE_LIMIT = 10
    MAX_RATE_LIMIT = 500
    RATE_LIMIT_HEADER = "X-RateLimit-Remaining"
    RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset"

    def setup_rate_limit_handler(self, sleep_for_rate=False, min_rate_to_sleep=MIN_RATE_LIMIT,
                                 rate_limit_header=RATE_LIMIT_HEADER,
                                 rate_limit_reset_header=RATE_LIMIT_RESET_HEADER):
        """Setup the rate limit handler.

        :param sleep_for_rate: sleep until rate limit is reset
        :param min_rate_to_sleep: minimun rate needed to make the fecthing process sleep
        :param rate_limit_header: header from where extract the rate limit data
        :param rate_limit_reset_header: header from where extract the rate limit reset data
        """
        self.rate_limit = None
        self.rate_limit_reset_ts = None
        self.sleep_for_rate = sleep_for_rate
        self.rate_limit_header = rate_limit_header
        self.rate_limit_reset_header = rate_limit_reset_header

        if min_rate_to_sleep > self.MAX_RATE_LIMIT:
            msg = "Minimum rate to sleep value exceeded (%d)."
            msg += "High values might cause the client to sleep forever."
            msg += "Reset to %d."
            self.min_rate_to_sleep = self.MAX_RATE_LIMIT
            logger.warning(msg, min_rate_to_sleep, self.MAX_RATE_LIMIT)
        else:
            self.min_rate_to_sleep = min_rate_to_sleep

    def sleep_for_rate_limit(self):
        """The fetching process sleeps until the rate limit is restored or
           raises a RateLimitError exception if sleep_for_rate flag is disabled.
        """
        if self.rate_limit is not None and self.rate_limit <= self.min_rate_to_sleep:
            seconds_to_reset = self.calculate_time_to_reset()

            if seconds_to_reset < 0:
                logger.warning("Value of sleep for rate limit is negative, reset it to 0")
                seconds_to_reset = 0

            cause = "Rate limit exhausted."
            if self.sleep_for_rate:
                logger.info("%s Waiting %i secs for rate limit reset.", cause, seconds_to_reset)
                time.sleep(seconds_to_reset)
            else:
                raise RateLimitError(cause=cause, seconds_to_reset=seconds_to_reset)

    def calculate_time_to_reset(self):
        """Calculate the seconds to reset the token requests."""

        raise NotImplementedError

    def update_rate_limit(self, response):
        """Update the rate limit and the time to reset
        from the response headers.

        :param: response: the response object
        """
        if self.rate_limit_header in response.headers:
            self.rate_limit = int(response.headers[self.rate_limit_header])
            logger.debug("Rate limit: %s", self.rate_limit)
        else:
            self.rate_limit = None

        if self.rate_limit_reset_header in response.headers:
            self.rate_limit_reset_ts = int(response.headers[self.rate_limit_reset_header])
            logger.debug("Rate limit reset: %s", self.calculate_time_to_reset())
        else:
            self.rate_limit_reset_ts = None
