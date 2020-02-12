# AsyncBilibiliDownloader
异步bilibili下载器，支持下载视频和番剧，基于`aiohttp`和`asyncio`的协程下载，速度飞快～

### 使用
- 如果下载视频，需实例化`VideoDownloader`类；如果下载番剧，需实例化`BangumiDownloader`类。
- 调用`run`方法即可开始下载

### 参数
- `aid`: 视频av号。
- `ep_id`: 番剧单集编号。
- `quality`: 视频质量。可选值为：112/116、74、80、64、32、16分，分别对应1080P+、720P+、1080P、720P、480P、360P。
- `fname`: 保存文件名，格式为flv。
- `max_tasks`: 协程数。
- `chunk_size`: 单个协程下载的视频大小，单位字节。
- `sess_data`: 用户Cookies中的SESSDATA，对于需要大会员才能观看的视频必须传入大会员的SESSDATA。
- `timeout`: 单个协程下载的时间限制，超出将重试。

### 说明
- `max_tasks`和`chunk_size`均不宜设置过大。建议设置两者的乘积为网络带宽大小（单位字节）。
- 建议使用Python3.6版本，Python3.7版本中有bug，可能会导致`aiohttp`中SSL验证失败。