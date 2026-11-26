# 交大云网站上线与 Editor 启用

## 首次启用

在交大云控制台确认当前实例是 `lab-web-prod-02`。现有网站运行在 `/opt/lab-web`，服务名为 `lab-web.service`。在已有的 root 终端逐条执行，确认前一条成功后再执行下一条。

```bash
cd /opt/lab-web
git pull --ff-only
bash scripts/jcloud_enable_handoff.sh
```

若终端使用有 sudo 权限的普通账号，在第三条前加 `sudo`。按提示为 `owner` 和 `editor` 分别输入并确认至少 16 个字符的强密码。使用密码管理器生成两个独立的密码。输入时屏幕保持空白。相同密码重复启用时会沿用原密码哈希。重新输入不同密码可以轮换对应账号，旧登录会话随之失效。

脚本为原服务添加专用 systemd 配置，沿用 `/opt/lab-web/.env` 和已绑定的 Supabase。草稿、附件索引和私有上传文件保存在 `/var/lib/lab-web-handoff/`。现有站点内容继续保存在 `site_content/`。凭证配置位于仅 root 可读的 `/etc/lab-web-handoff/env`。配置和私有工作数据都放在 Git 工作树之外。

脚本录入密码前先检查本机 Nginx。重启后，它通过本机 Nginx 检查 `/admin` 跳转到 `/editor/login`，并确认登录页返回 200。只有本机启用检查失败时才恢复原配置并重启服务。自动恢复失败时，终端会显示仅 root 可读的备份目录。排查可用 `journalctl -u lab-web.service -n 60 --no-pager`。

校园代理或主机访问公网官网超时，会影响从主机发起的公网检查。因此脚本用本机回环地址验证 Nginx 到 Gunicorn 的服务。看到 `Local Nginx and service verified` 后，从自己的电脑登录正式后台，完成官网入口和两种账号的验收。

## 账号验收与交接

从[网站后台](https://opticompt-lab.sjtu.edu.cn/admin)登录。用两个浏览器会话分别查验账号。

- `owner` 是站长账号。核对内容、素材、设置和统计入口，以及 Git 和备份功能。
- `editor` 用于学姐日常维护。核对论文、人员和新闻的草稿预览，以及首页和招生文案入口。在这个会话直接进入 `/admin/settings` 或 `/admin/analytics` 应收到拒绝访问。

把后台地址、`editor` 账号和学姐的说明书发给学姐。密码另用私密渠道交接。可以请她先保存一条真实待更新的草稿并打开预览，核对内容后再决定是否公开。

## 日常更新与回退

后续代码更新通常只需在 `/opt/lab-web` 执行 `git pull --ff-only`，随后执行 `systemctl restart lab-web.service` 并检查公开首页和后台。已有正式账号继续使用原密码。

如需撤下新版登录，在 `/opt/lab-web` 的 root 终端执行：

```bash
bash scripts/jcloud_enable_handoff.sh --rollback
```

该操作撤下 Editor 配置、重启服务，随后通过本机 Nginx 核对原登录入口。草稿目录、公开内容、Supabase 数据和当前代码提交都会保留。这项操作适合回退登录方式。若需要回退代码，由站长另行选定已知可用提交并检查线上内容改动。

## 可选的 SSH 交接

先从交大云控制台的目标实例读取公开主机密钥指纹：

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

用这个指纹比对 SSH 客户端首次连接时展示的 ED25519 指纹，确认一致后再登录。主机密钥指纹可用于核对目标实例。管理员密码和私钥留在各自的密钥管理位置。
