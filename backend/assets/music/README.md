# 背景音乐床(bgm)

图生视频「音乐床」从这个目录随机挑曲子,垫在旁白/音效**之下**(见 `app/services/video_edit.py` 的 `pick_music` / `add_music_bed`)。

## 怎么用
1. 往本目录放几首 **CC0(公共领域,免署名、可商用)** 的 bgm:`.mp3 / .m4a / .ogg / .wav / .aac`。
2. 开关:`.env` 里 `POD_VIDEO_MUSIC=true`,重启 worker。
3. 没放曲子也不会报错——目录为空时自动跳过(no-op)。

## 授权要求(商用,务必)
本项目是**商用** POD 带货视频,音乐会混进对外交付的视频里。**只用可商用授权的曲子**,首选 **CC0**:
- **Pixabay Music → CC0 区**:<https://pixabay.com/music/search/cc0/> (免署名、可商用,最省心,推荐)
- **Freesound** 按 `Creative Commons 0` 过滤:<https://freesound.org/search/?f=license:%22Creative+Commons+0%22> (有 API)
- **Free Music Archive** 的 CC0 / 公共领域条目

⚠️ 不要用 CC-BY(要署名)、NC(非商用)、或来源不明的曲子。建议放 **3~8 首** 不同节奏/情绪,随机挑增加多样性。

## 为什么不入 git
音乐文件体积大 + 授权需逐曲确认,**不提交进仓库**(`.gitignore` 已忽略本目录的音频文件,只保留本 README)。
由运维把选好的 CC0 曲子直接放到**服务器**的本目录(`/www/wwwroot/podsys/backend/assets/music/`)。
