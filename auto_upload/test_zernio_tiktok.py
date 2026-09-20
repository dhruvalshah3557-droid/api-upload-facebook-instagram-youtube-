import unittest
from unittest.mock import Mock, patch
# Existing suite provides offline dependency stubs for the production modules.
import test_delivery_policy
import optimized_runner
from tiktok_uploader import TikTokUploader, TikTokDeliveryUncertain
from job_generator import generate_jobs

class TikTokTests(unittest.TestCase):
    def setUp(self):
        self.u = TikTokUploader('test-key', account_id='account-123', job_id='sku-account-video')
        self.info = {'creator': {'canPostMore': True}, 'privacyLevels': [{'value':'PUBLIC_TO_EVERYONE'}]}

    def response(self, post):
        r = Mock(status_code=201)
        r.json.return_value = {'post': post}
        return r

    @patch('tiktok_uploader.requests.post')
    def test_video_uses_caption_public_own_brand_and_stable_request_id(self, post):
        post.return_value = self.response({'_id':'p1','platforms':[{'platform':'tiktok','status':'published','platformPostUrl':None}]})
        with patch.object(self.u, 'creator_info', return_value=self.info):
            self.assertEqual(self.u.upload('https://cdn.test/a.mp4',description='same SKU caption')['url'], '')
            first = post.call_args.kwargs
            self.u.upload('https://cdn.test/a.mp4',description='same SKU caption')
        self.assertEqual(first['headers']['x-request-id'],post.call_args.kwargs['headers']['x-request-id'])
        self.assertEqual(first['json']['content'],'same SKU caption')
        self.assertEqual(first['json']['tiktokSettings']['privacy_level'],'PUBLIC_TO_EVERYONE')
        self.assertEqual(first['json']['tiktokSettings']['commercialContentType'],'brand_organic')

    @patch('tiktok_uploader.requests.post')
    def test_photos_keep_order_and_full_caption(self, post):
        post.return_value = self.response({'_id':'p2','platforms':[{'platform':'tiktok','status':'published'}]})
        urls=['https://cdn.test/center.jpg','https://cdn.test/side.jpg']
        with patch.object(self.u,'creator_info',return_value=self.info):
            self.u.upload_carousel(urls,'caption '*40)
        body=post.call_args.kwargs['json']
        self.assertEqual([m['url'] for m in body['mediaItems']], urls)
        self.assertEqual(body['tiktokSettings']['description'],'caption '*40)

    @patch('tiktok_uploader.requests.post')
    def test_pending_response_is_not_success(self, post):
        post.return_value=self.response({'_id':'p3','platforms':[{'platform':'tiktok','status':'pending'}]})
        with patch.object(self.u,'creator_info',return_value=self.info), self.assertRaises(TikTokDeliveryUncertain):
            self.u.upload('https://cdn.test/a.mp4')

    @patch('tiktok_uploader.requests.get')
    def test_private_only_account_blocks_public_post(self, get):
        get.return_value.json.return_value={'privacyLevels':[{'value':'SELF_ONLY'}]}
        with self.assertRaises(ValueError): self.u.creator_info('video')

    def test_generates_photos_and_videos(self):
        source={'images':['https://cdn.test/a.jpg'],'video_url':'https://cdn.test/a.mp4','model_videos':[],'model_images':[]}
        jobs=generate_jobs({'1':source},[{'enabled':True,'platform':'tiktok','account_id':'TIKTOK-CD','platform_account_id':'6aae50288d284ffb211a67e9'}])
        self.assertEqual({j['format'] for j in jobs},{'carousel','video'})

    def test_scheduler_reserves_tiktok_slot(self):
        accounts={'TIKTOK-CD':{'enabled':True,'platform':'tiktok','platform_account_id':'a'}}
        self.assertEqual(optimized_runner._platform_limits(50,accounts)['tiktok'],1)
        self.assertTrue(optimized_runner._is_ambiguous_delivery_error(optimized_runner.main.DeliveryUncertainError('pending')))

if __name__=='__main__': unittest.main()
