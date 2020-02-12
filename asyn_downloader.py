#!/usr/bin/env python
# -*- coding:utf-8 -*-

import asyncio
import aiohttp
import logging
import json
import time
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

class WrongPageError(Exception):
    pass

class HTTPStatusError(Exception):
    pass

class ApiStatusError(Exception):
    pass

class _BaseDownloader:
    '''
        Base Class of downloader, don't use it directly!
    '''
    def __init__(self, quality, fname, max_tasks=6, chunk_size=524288, sess_data=None, timeout=10):
        ''' 
            Initialization. If you use the _BaseDownloader directly, it raises NotImplementedError.
            :param aid: Av index.
            :param quality: Quility index in [112, 74, 80, 64, 32, 16].
            :param fname: File to save.
            :param max_tasks: Number of corotines.
            :param chunk_size: Bytes number per download (524288=0.5M).
            :sess_data: SESSDATA in cookies.
            :timeout: Timeout of downloads.
        '''
        # self.aid = aid
        self.quality = quality
        self.fname = fname
        self.max_tasks = max_tasks
        self.chunk_size = chunk_size

        self.queue = asyncio.Queue()
        if sess_data is None:
            cookies = None
        else:
            cookies = {'SESSDATA': sess_data}
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), cookies=cookies)

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:56.0) Gecko/20100101 Firefox/56.0',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'identity',
            'Range': 'bytes=0-1',
            'Origin': 'https://www.bilibili.com',
            'Connection': 'keep-alive'
        }

        self.cid = ''
        self.title = ''
        self.subtitle = ''
        self.blocks = []
        self.content = b''
        self._chunks = []

        self._timestamp = 0
        self._download_start_time = 0
        self._current_size = 0
        self._size = 0

        raise NotImplementedError('Don\'t use _BaseDownloader directly!')

    def _download_api(self):
        raise NotImplementedError('Don\'t use _BaseDownloader directly!')

    async def _get_check(self, excepted_code, url, headers=None):
        resp = await self.session.get(url, headers=headers)
        if resp.status != excepted_code:
            await resp.release()
            raise HTTPStatusError('Expected HTTP code {} but {} is gotten!'.format(excepted_code, resp.status))
        return resp

    async def _get_download_url(self):
        logging.debug('Getting download link ...')
        url = self._download_api()
        resp = await self._get_check(200, url, self.headers)
        infos = json.loads(await resp.text())
        await resp.release()
        if infos['code'] != 0:
            raise ApiStatusError(infos['message'])

        try:
            infos = infos['data']
        except KeyError:
            infos = infos['result']

        logging.debug('Link of quality {} is gotten.'.format(infos['quality']))
        if self.quality not in infos['accept_quality']:
            logging.warning('Quality index should in {}. Link of quality {} is gotten.'.format(infos['accept_quality'], infos['quality']))
        elif self.quality != infos['quality']:
            logging.warning('No permissions to get quality {}, you should pass SESSDATA of vip account. Link of quality {} is gotten.'.format(self.quality, infos['quality']))
        self.quality = infos['quality']

        for item in infos['durl']:
            order = item['order']
            size = item['size']
            obj_urls = item['backup_url']
            obj_urls.append(item['url'])
            self.blocks.append((order, size, obj_urls))
            self._size += size

        if len(infos['durl']) > 1:
            logging.warning('There are {} sub-flv files. This may cause some unexcepted errors.'.format(len(infos['durl'])))

        return 0

    async def _prepare(self):
        '''
            Get the download link.
        '''
        raise NotImplementedError('Don\'t use _BaseDownloader directly!')

    def _add_to_queue(self):
        logging.debug('Adding item to queue ...')
        for order, size, obj_urls in self.blocks:
            if size % self.chunk_size == 0:
                chunk_num = int(size / self.chunk_size)
            else:
                chunk_num = int(size / self.chunk_size) + 1
            for i in range(chunk_num):
                obj_url = obj_urls[i % len(obj_urls)]
                start = i * self.chunk_size
                end = min((i + 1) * self.chunk_size - 1, size - 1)
                self.queue.put_nowait((order, obj_url, start, end))
        logging.debug('Add complete.')

    async def download(self):
        '''
            Main coroutine. Tha main coroutine should handle the exception raised by sub-coroutines.
        '''
        
        # Get infos.
        logging.info('Preparing ...')
        try:
            await self._prepare()
        except Exception as e:
            logging.error(e)
            return -1

        # Add to the queue.
        self._add_to_queue()

        # Start.
        logging.info('Start downloading ...')
        self._timestamp = time.time()
        self._download_start_time = self._timestamp

        workers = [asyncio.Task(self._work()) for _ in range(self.max_tasks)]
        await self.queue.join()
        print(' ' * 100, end='\r')
        logging.info('Complete! Elaspe time: {:.2f}s'.format(time.time() - self._download_start_time))
        for worker in workers:
            worker.cancel()
        await self.session.close()

        # Save.
        self._chunks.sort(key=lambda x: (x[0], x[1]))
        self.content = b''.join([chunk[-1] for chunk in self._chunks])
        with open(self.fname, 'wb') as f:
            f.write(self.content)
        logging.info('Written in {}'.format(self.fname))

        return 0

    async def _work(self):
        while True:
            order, obj_url, start, end = await self.queue.get()
            chunk = await self._download_chunk(order, obj_url, start, end)
            self._chunks.append((order, start, chunk))
            self.queue.task_done()

            # 计算速度
            self._current_size += len(chunk)
            now = time.time()
            sudden_speed = len(chunk) / ((now - self._timestamp))
            self._timestamp = now
            avg_speed = self._current_size / ((now - self._download_start_time))
            print(' ' * 100, end='\r')
            print('Progress : {}/{}({:>5.2f}%) | Sudden speed : {}/s | Average speed : {}/s'.format(format_size(self._current_size), format_size(self._size), self._current_size * 100 / self._size, format_size(sudden_speed), format_size(avg_speed)), end='\r')

    async def _download_chunk(self, order, obj_url, start, end):
        logging.debug('Downloading chunk{}[{}-{}] ...'.format(order, start, end))
        headers = self.headers.copy()
        headers['Range'] = 'bytes={}-{}'.format(start, end)
        while True:
            try:
                resp = await self._get_check(206, obj_url, headers)
                # resp = await self.session.get(obj_url, headers=headers)
            except asyncio.TimeoutError as e:
                logging.warning('TimeoutError raised when downloading chunk{}[{}-{}]. Retring ...'.format(order, start, end))
                continue
            except HTTPStatusError as e:
                logging.warning(e + 'Retring ...')
                continue

            try:
                content = await resp.read()
            except asyncio.TimeoutError as e:
                logging.warning('TimeoutError raised when downloading chunk{}[{}-{}]. Retring ...'.format(order, start, end))
                await resp.release()
                continue

            logging.debug('Chunk{}[{}-{}] is downloaded.'.format(order, start, end))
            await resp.release()
            return content

    def run(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.download())

class VideoDownloader(_BaseDownloader):

    def __init__(self, aid, quality, fname, page=1, max_tasks=6, chunk_size=524288, sess_data=None, timeout=10):
        ''' 
            :param aid: Av index.
            :param quality: Quility index in [80, 64, 32, 16].
            :param page: Page index.
            :param fname: File to save.
            :param max_tasks: Number of corotines.
            :param chunk_size: Bytes number per download (524288=0.5M).
            :sess_data: SESSDATA in cookies.
            :timeout: Timeout of downloads.
        '''
        try:
            super().__init__(quality, fname, max_tasks, chunk_size, sess_data, timeout)
        except NotImplementedError:
            pass
        self.aid = aid
        self.page = page
        self.pc_url = 'https://www.bilibili.com/video/av{}'.format(aid)
        self.headers['Referer'] = self.pc_url

    def _cid_api(self):
        return 'https://api.bilibili.com/x/player/pagelist?aid={}'.format(self.aid)

    def _download_api(self):
        return 'https://api.bilibili.com/x/player/playurl?avid={}&cid={}&qn={}'.format(self.aid, self.cid, self.quality)

    async def _prepare(self):
        await self._get_cid()
        await self._get_download_url()
        
        return 0

    async def _get_cid(self):
        logging.debug('Getting cid ...')
        url = self._cid_api()
        resp = await self._get_check(200, url)
        infos = json.loads(await resp.text())['data']
        self.subtitle = infos[self.page - 1]['part']
        await resp.release()
        try:
            cid = infos[self.page - 1]['cid']
        except KeyError:
            raise WrongPageError('av{} has {} pages but page {} is required!'.format(self.aid, len(infos), self.page))
        logging.debug('cid gotten : {}'.format(cid))
        self.cid = cid
        
        return 0

class BangumiDownloader(_BaseDownloader):

    def __init__(self, ep_id, quality, fname, max_tasks=6, chunk_size=524288, sess_data=None, timeout=10):
        ''' 
            :param aid: Episode index.
            :param quality: Quility index in [80, 64, 32, 16].
            :param page: Page index.
            :param fname: File to save.
            :param max_tasks: Number of corotines.
            :param chunk_size: Bytes number per download (524288=0.5M).
            :sess_data: SESSDATA in cookies.
            :timeout: Timeout of downloads.
        '''
        try:
            super().__init__(quality, fname, max_tasks, chunk_size, sess_data, timeout)
        except NotImplementedError:
            pass
        self.ep_id = ep_id
        self.pc_url = 'https://www.bilibili.com/bangumi/play/ep{}'.format(ep_id)
        self.headers['Referer'] = self.pc_url

    def _download_api(self):
        return 'https://api.bilibili.com/pgc/player/web/playurl?ep_id={}&qn={}'.format(self.ep_id, self.quality)

    async def _prepare(self):
        await self._get_download_url()
        return 0

def format_size(size):
    size = float(size)
    if size < 1024:
        return '{:>6.2f}B'.format(size)
    elif 1024 <= size < 1024 ** 2:
        return '{:>6.2f}KB'.format(size / 1024)
    elif 1024 ** 2 <= size < 1024 ** 3:
        return '{:>6.2f}MB'.format(size / (1024 ** 2))
    elif 1024 ** 3 <= size:
        return '{:>6.2f}GB'.format(size / (1024 ** 3))

if __name__ == '__main__':

    SESSDATA = 'eaf00f06%2C1583914773%2Cc7708f21'
    quality = 116
    fname = './test.flv'

    url = input('Please input the url of the video: ')

    try:
        aid = re.search('av(\d+)', url).group(1)
        downloader = VideoDownloader(aid, quality, fname, sess_data=SESSDATA)
        download.run()
    except AttributeError:
        try:
            ep_id = re.search('ep(\d+)', url).group(1)
            downloader = BangumiDownloader(ep_id, quality, fname, sess_data=SESSDATA)
            downloader.run()
        except AttributeError:
            logging.error('Wrong url format.')


