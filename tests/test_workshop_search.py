import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import core.workshop as workshop


class WorkshopSearchTests(unittest.TestCase):
    def test_public_second_page_uses_ranked_results_not_links_in_descriptions(self):
        args = {"appid": 4348910, "page": 2}
        item = {"publishedfileid": "1234567890", "consumer_appid": 4348910, "title": "Results page 2",
                "short_description": "See /sharedfiles/filedetails/?id=9999999999", "tags": [{"tag": "Gameplay"}]}
        state = {"route": "/workshop/browse", "renderContext": {"queryData": json.dumps({"queries": [
            {"queryKey": ["workshop_browse", args, 1], "state": {"data": {"eresult": 1, "current_page": 2,
                "total_pages": 3, "total_count": 65, "results": [item, item, {**item, "consumer_appid": 42}]}}}
        ]})}}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = ('<script type="application/json" id="valve-ssr-data">'
            + json.dumps(state) + '</script>').encode()
        with patch.object(workshop, '_steam_open', return_value=response) as fetch:
            result = workshop.browse_workshop_public_page("关键词", "Gameplay", page=2)
        self.assertEqual((result.total, result.has_next), (65, True))
        self.assertEqual([(m.published_file_id, m.title, m.tags) for m in result.items],
                         [("1234567890", "Results page 2", ["Gameplay"])])
        query = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query)
        self.assertEqual(query['p'], ['2'])
        self.assertEqual(query['searchtext'], ['关键词'])
        self.assertEqual(query['browsesort'], ['textsearch'])
        self.assertEqual(query['requiredtags[]'], ['Gameplay'])

    def test_empty_public_page_is_distinct_from_an_unrecognized_response(self):
        response = MagicMock()
        read = response.__enter__.return_value.read
        with patch.object(workshop, '_steam_open', return_value=response):
            read.return_value = b'<html>Service unavailable</html>'
            with self.assertRaises(ValueError):
                workshop.browse_workshop_public_page('any')
            read.return_value = b'<div class="workshopBrowseItems">No items matching</div>'
            self.assertEqual(workshop.browse_workshop_public_page('missing').items, [])

    def test_no_proxy_alone_does_not_disable_windows_system_proxy(self):
        net = workshop.urllib.request
        request = net.Request('https://steamcommunity.com/workshop/browse/')
        proxies = {'https': 'http://127.0.0.1:9990'}
        with (patch.object(workshop.sys, 'platform', 'win32'),
              patch.object(net, 'getproxies_environment', return_value={'no': 'localhost'}),
              patch.object(net, 'proxy_bypass_environment', return_value=False) as bypass,
              patch.object(net, 'proxy_bypass_registry', return_value=False, create=True),
              patch.object(net, 'getproxies_registry', return_value=proxies, create=True),
              patch.object(net, 'build_opener') as opener, patch.object(net, 'urlopen') as direct):
            workshop._steam_open(request, 6)
            self.assertEqual(opener.call_args.args[0].proxies, proxies)
            opener.return_value.open.assert_called_once_with(request, timeout=6)
            direct.assert_not_called()
            bypass.return_value = True
            workshop._steam_open(request, 6)
            direct.assert_called_once_with(request, timeout=6)
