# Codex Local Port Registry

这是一个给 Codex 用的本地 skill，用来解决“多个项目同时开发时反复抢 `3000` / `3001` / `5173` / `10086` 等默认端口”的问题。

典型场景：

- 你有很多前端、后端、Vite、Next.js、Docker Compose 项目
- AI 每次一启动 `npm run dev`、`pnpm dev`、`next dev`、`vite`、`docker compose up` 就和别的项目撞端口
- 你不想每次手动查 `lsof -i :3000`
- 你也不想 AI 未经确认就直接改掉项目配置

这个 skill 的目标不是“强行分配端口”，而是让 AI 在启动前先做一次端口审计，然后按下面的规则执行：

1. 先检查当前项目声明的端口
2. 对比本地端口注册表，看是否和其他项目冲突
3. 如果冲突，生成一段可以直接发给用户的确认提示
4. 只有用户明确同意后，才改 `.env`、`package.json` 或 `docker-compose.yml`
5. 把新的端口结果写回本地 registry，避免以后继续乱抢

## 这个库到底解决什么问题

它主要解决 3 类问题：

### 1. 启动前不知道会不会撞端口

很多项目默认就是：

- Next.js: `3000`
- Node API: `3001`
- Vite: `5173`
- 某些 Taro / H5 项目: `10086`

当你机器上有很多仓库时，这些默认值几乎一定会撞。

### 2. AI 会直接用默认端口启动，直到报错

没有这套 skill 时，AI 往往会：

- 直接跑 `npm run dev`
- 等端口占用报错
- 再临时猜一个端口

这样既慢，也不稳定，而且不同项目之间没有统一登记。

### 3. 端口修复缺少“先提醒、再修改”的约束

这个 skill 强制把流程改成：

- 先提醒用户冲突了哪些项目
- 再给出建议新端口
- 最后等用户确认后才修改配置

也就是说，它默认是“提示型”，不是“自动乱改型”。

## 安装

```bash
npx github:kylinzhao/codex-local-port-registry
```

安装完成后重启 Codex。

如果你想覆盖已有安装：

```bash
npx github:kylinzhao/codex-local-port-registry -- --force
```

## 它安装了什么

安装器会把 skill 拷贝到：

```bash
~/.codex/skills/local-port-registry
```

其中核心文件包括：

- `SKILL.md`: skill 说明和使用规则
- `agents/openai.yaml`: UI 元数据
- `scripts/port_registry.py`: 端口扫描、预检、提示、修复逻辑

## AI 实际会怎么提醒

例如某个项目当前配置是 `3000`，而这个端口已经被其他项目占用了，AI 会先生成类似这样的文案：

> 项目 `cv/portfolio` 当前端口 `3000` 与 `gogogo/web`、`i18n/global-station`、`notellm/backend` 等项目冲突。建议改用新端口 `17569`。是否应用这个新端口？

重点是：

- 会告诉你“和谁冲突”
- 会给出“建议新端口”
- 不会直接改

## 它什么时候会改配置

只有在用户明确同意之后，AI 才应该执行修复命令。

修复目标可能包括：

- `.env`
- `.env.local`
- `.env.development`
- `package.json` 里的 `dev` / `preview` 脚本
- `docker-compose.yml` 或 `docker-compose.yaml`

默认不会改文档，不会静默重写项目配置。

## 推荐接入方式

如果你希望 Codex 在每次准备启动本地服务前都先做端口预检，把下面这段加到你的全局 `AGENTS.md`：

~~~md
## Local Dev Port Guard

Before starting any local dev server, preview server, backend watcher, or `docker compose` service, run:

```bash
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" prompt --project "$PWD" --command "<start command>"
```

If `needs_repair=true`, show `user_prompt`, wait for approval, then run `apply_command`.
If `needs_repair=false`, use `recommended_command` when present.
~~~

## 可选配置

如果你希望冲突提示里显示更短的相对路径，而不是完整绝对路径，可以设置：

```bash
export LOCAL_PORT_REGISTRY_WORKSPACE_ROOTS="$HOME/work:$HOME/projects"
```

这样提示里的项目名会更短，更适合直接展示给用户。
