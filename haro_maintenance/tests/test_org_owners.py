import sys, unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gitops import owner_authorized
from github_api import GitHub, ApiError
from config import Error

class OrgOwnersTests(unittest.TestCase):
    def test_structured_owner_not_title_or_comment(self):
        raw=b"owners:\n- username: '@beclab'\n  title: TShentu\n# TShentu\n"
        self.assertFalse(owner_authorized(raw, "TShentu"))
        check=Mock(return_value=True)
        self.assertTrue(owner_authorized(raw,"TShentu",check))
        check.assert_called_once_with("beclab")
        self.assertTrue(owner_authorized(b"owners:\n- username: '@TSHENTU'\n", "tshentu"))
        self.assertTrue(owner_authorized(b"tester\n", "tester"))
        self.assertFalse(owner_authorized(b"title: TShentu", "TShentu", check))
    def test_membership_requires_active_matching_identity_and_org(self):
        for state,user,org,expected in [("active","TShentu","beclab",True),("pending","TShentu","beclab",False),("active","someone","beclab",False),("active","TShentu","other",False)]:
            api=GitHub("unused");api.request=Mock(return_value={"state":state,"user":{"login":user},"organization":{"login":org}})
            self.assertEqual(api.is_org_member("beclab","TShentu"),expected)
            self.assertEqual(api.is_org_member("BECLAB","tshentu"),expected)
            self.assertEqual(api.request.call_count,1)
    def test_unverifiable_membership_does_not_authorize(self):
        for code in (403,404,502):
            api=GitHub("unused");api.request=Mock(side_effect=ApiError(code,"membership"))
            if code==404:self.assertFalse(api.is_org_member("beclab","TShentu"))
            else:
                with self.assertRaises(Error):api.is_org_member("beclab","TShentu")
