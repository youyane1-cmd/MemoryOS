# Git 和 GitHub 使用指南

这份文档面向刚开始接触 Git/GitHub 的开发者，重点解释日常开发中最容易混淆的概念：本地仓库、远程仓库、提交历史、拉取、推送、回退、撤销、stash、冲突处理。

文档里的命令默认在项目根目录执行，例如：

```bash
cd D:\MemoryOS
```

## 1. 先理解 Git 和 GitHub 的关系

Git 是本地版本管理工具。它运行在你的电脑上，负责记录代码的每一次提交。

GitHub 是远程代码托管平台。它保存一份远程仓库，方便备份、协作、查看历史、创建 Pull Request。

可以这样理解：

```text
你的电脑本地仓库  <----同步---->  GitHub 远程仓库
```

本地仓库和 GitHub 远程仓库不是同一个东西。你本地改了代码，如果不 commit、不 push，GitHub 是不知道的。

## 2. 工作区、暂存区、提交历史

Git 里有三个常见区域：

```text
工作区 working tree
  你正在编辑的文件

暂存区 staging area
  准备放进下一次 commit 的改动

提交历史 commit history
  已经正式记录下来的版本
```

最常见流程是：

```bash
git status
git add .
git commit -m "your message"
git push
```

含义是：

```text
git status    看当前有哪些改动
git add .     把改动放进暂存区
git commit    生成一次本地提交
git push      把本地提交推送到 GitHub
```

## 3. 每天最常用的命令

### 查看当前状态

```bash
git status
```

你应该经常运行这个命令。它会告诉你：

- 当前在哪个分支
- 有没有未提交改动
- 有没有文件未被 Git 跟踪
- 本地分支和远程分支是否同步

### 查看提交历史

```bash
git log --oneline
```

示例：

```text
9fc549c update eval API progress docs
0f1b963 previous version
```

每一行前面的短字符串就是 commit id。比如 `9fc549c` 和 `0f1b963`。

### 查看具体改了什么

查看未提交改动：

```bash
git diff
```

查看已经暂存的改动：

```bash
git diff --staged
```

查看某个提交的内容：

```bash
git show 9fc549c
```

## 4. fetch、pull、push 的区别

### git fetch

```bash
git fetch
```

只把 GitHub 上的最新提交信息下载到本地，不会改你当前文件。

适合先看看远程有没有新内容。

### git pull

```bash
git pull
```

等价于：

```text
git fetch + 把远程更新合进当前分支
```

它会修改你的本地代码，所以如果你本地有未提交改动，可能失败。

常见报错：

```text
Your local changes to the following files would be overwritten by merge
```

意思是：你本地有改动，Git 担心 pull 会覆盖它们。

### git push

```bash
git push
```

把本地已经 commit 的内容推送到 GitHub。

如果你只是改了文件，但没有 commit，`git push` 不会把这些工作区改动推上去。

## 5. 本地有改动时怎么 pull

本地有改动时，一般有三种处理方式。

### 方式一：本地改动不要了

谨慎使用：

```bash
git reset --hard
git pull
```

`git reset --hard` 会丢掉本地未提交改动。

### 方式二：本地改动要保留，但暂时不提交

推荐使用 stash：

```bash
git stash
git pull
git stash pop
```

含义：

```text
git stash      把当前未提交改动临时收起来
git pull       拉取远程更新
git stash pop  把刚才收起来的改动拿回来
```

如果 `stash pop` 后发生冲突，需要手动解决冲突。

### 方式三：本地改动已经准备好了

先提交，再 pull：

```bash
git add .
git commit -m "describe your change"
git pull
git push
```

如果 pull 时远程也改了同一部分代码，可能需要解决冲突。

## 6. commit、push 之后还能回退吗

可以。GitHub 会记录每一次提交历史。

比如你看到：

```text
0f1b963..9fc549c  main -> main
```

意思是远程 `main` 从 `0f1b963` 更新到了 `9fc549c`。

现在你有几个选择。

## 7. 只回退本地，不改 GitHub

如果你只是想让自己电脑上的代码回到旧版本：

```bash
git reset --hard 0f1b963
```

这只影响本地仓库。GitHub 上仍然是新的 `9fc549c`。

注意：如果之后你再执行：

```bash
git pull
```

远程的 `9fc549c` 可能又会被拉回来。

## 8. 已经 push 到 GitHub，推荐怎么撤销

推荐用 `git revert`。

```bash
git revert 9fc549c
git push
```

它会新增一个反向提交，把 `9fc549c` 的代码改动撤销掉。

历史会变成：

```text
0f1b963 -> 9fc549c -> revert commit
```

最终代码内容回到了 `0f1b963` 的效果，但 GitHub 上仍然能看到发生过 `9fc549c` 这次提交。

这是最安全的远程撤销方式，尤其适合 `main` 分支。

## 9. 强行让 GitHub 回到旧提交

不推荐新手在 `main` 上使用，但你需要知道它是什么：

```bash
git reset --hard 0f1b963
git push --force-with-lease origin main
```

它会让远程 `main` 指回旧提交，看起来像把后面的提交从主线历史中拿掉。

风险：

- 会改远程历史
- 如果别人已经基于新提交开发，会影响别人
- 对 `main` 分支尤其危险

如果不是非常确定，优先使用 `git revert`。

## 10. reset 的三种常见模式

### soft

```bash
git reset --soft HEAD~1
```

撤销最近一次 commit，但保留改动在暂存区。

适合：刚 commit 完发现 commit message 写错了，或者想重新整理提交。

### mixed

```bash
git reset HEAD~1
```

撤销最近一次 commit，保留改动在工作区，但不在暂存区。

### hard

```bash
git reset --hard HEAD~1
```

撤销最近一次 commit，并丢掉代码改动。

这是危险操作，执行前一定确认改动不需要了。

## 11. revert 和 reset 的区别

`revert` 是新增一个反向提交，不改历史。

`reset` 是移动当前分支指针，会改变本地历史；如果配合 force push，还会改变远程历史。

简单记忆：

```text
已经 push 到 GitHub：优先 revert
还没 push，只在本地：可以 reset
```

## 12. stash 的常见用法

临时保存改动：

```bash
git stash
```

查看 stash 列表：

```bash
git stash list
```

恢复最近一次 stash：

```bash
git stash pop
```

只应用但不删除 stash：

```bash
git stash apply
```

删除某个 stash：

```bash
git stash drop stash@{0}
```

清空所有 stash：

```bash
git stash clear
```

## 13. 分支是什么

分支可以理解为一条开发线。

查看当前分支：

```bash
git branch
```

创建新分支：

```bash
git checkout -b feature/api-progress
```

切回 main：

```bash
git checkout main
```

现代 Git 也可以用：

```bash
git switch main
git switch -c feature/api-progress
```

建议日常开发不要直接在 `main` 上改，可以创建 feature 分支：

```bash
git checkout -b feature/memory-progress
```

开发完成后 push：

```bash
git push -u origin feature/memory-progress
```

然后在 GitHub 上开 Pull Request。

## 14. 冲突是什么

冲突通常发生在：

- 你本地改了某个文件
- 远程也改了同一个文件的同一块内容
- Git 不知道该保留哪一份

冲突文件里可能出现：

```text
<<<<<<< HEAD
你的本地内容
=======
远程内容
>>>>>>> origin/main
```

你需要手动编辑成最终正确内容，然后：

```bash
git add conflicted_file.py
git commit
```

如果是 rebase 或 merge 过程，Git 会提示下一步该执行什么命令。

## 15. 最稳的日常开发流程

一个适合个人项目和小团队的流程：

```bash
git status
git pull
git checkout -b feature/my-change

# 修改代码

git status
git diff
git add .
git commit -m "describe the change"
git push -u origin feature/my-change
```

然后在 GitHub 上开 PR，确认没问题后合并。

如果你暂时只在 `main` 上开发，也至少养成这个习惯：

```bash
git status
git pull
# 修改代码
git diff
git add .
git commit -m "describe the change"
git push
```

## 16. 常见场景速查

### 场景：我本地改乱了，想回到上一次 commit

```bash
git reset --hard
```

会丢掉未提交改动。

### 场景：我刚 commit 了，但还没 push，想撤销 commit 保留代码

```bash
git reset --soft HEAD~1
```

### 场景：我刚 commit 了，但还没 push，想撤销 commit 且不要代码

```bash
git reset --hard HEAD~1
```

### 场景：我已经 push 了，想撤销这次改动

```bash
git revert HEAD
git push
```

### 场景：我想看看 GitHub 有没有新提交，但不想改本地代码

```bash
git fetch
git status
```

### 场景：我本地有改动，但想先拉远程

```bash
git stash
git pull
git stash pop
```

### 场景：我想看某个文件是谁改的

```bash
git blame path/to/file.py
```

### 场景：我想看最近 5 次提交

```bash
git log --oneline -5
```

## 17. 对 main 分支的建议

`main` 通常代表稳定版本。建议：

- 不要随便 `push --force` 到 `main`
- 已经 push 的错误优先用 `git revert`
- 重要改动先开分支
- push 前先 `git status` 和 `git diff`
- 不确定时先 `git fetch`，不要直接 `pull`

## 18. 推荐学习顺序

先学这些：

1. `git status`
2. `git add`
3. `git commit`
4. `git push`
5. `git pull`
6. `git log`
7. `git diff`
8. `git stash`
9. `git revert`
10. `git reset`

再学这些：

1. branch
2. merge
3. rebase
4. Pull Request
5. conflict resolution
6. tag
7. release

## 19. 一个安全判断原则

执行命令前问自己：

```text
这个命令会不会丢掉本地未提交改动？
这个命令会不会改远程 GitHub 历史？
这个分支是不是 main？
```

如果答案不确定，先执行：

```bash
git status
git log --oneline -5
```

再决定下一步。

