## 1. 安装 uv 包管理器
uv 是快速 Python 包管理工具，Basic Memory 官方推荐使用。

在 PowerShell 中执行：
```shell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

验证安装：
```shell
uv --version
```
# 应显示类似 uv 0.6.9
安装完成后，关闭并重新打开 PowerShell 以使环境变量生效。

## 2. 安装 Basic Memory

### 2.1 使用 uv 安装
在 PowerShell 中执行：

```shell
uv tool install basic-memory
```
### 2.2 验证安装

```shell
basic-memory --version
```
# 应显示类似 basic-memory, version 0.19.x

### 2.3 找到 uvx 完整路径（重要）
后续配置需要用到 uvx 的完整路径，先找出来：

```shell
where uvx
```

典型输出：
C:\Users\你的用户名\.local\bin\uvx.exe
记下这个路径，后面配置 MCP 时会用到。

## 3. 配置项目级 MCP

### 3.1 创建项目结构
假设你的项目在 D:\Projects\MyPythonApp，首先创建必要的目录：

```shell
cd D:\Projects\MyPythonApp
mkdir .cursor
```

3.2 创建 MCP 配置文件
在项目根目录下创建 .cursor\mcp.json 文件。


配置文件内容：
```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "C:\\Users\\你的用户名\\.local\\bin\\uvx.exe",
      "args": [
        "basic-memory", 
        "mcp",
        "--project",
        "my-python-app",
        "--data-dir",
        "${workspaceFolder}/.basic-memory"
      ]
    }
  }
}
```
重要修改：

将 C:\\Users\\你的用户名\\.local\\bin\\uvx.exe 替换为第 2.3 步中查到的实际路径

--project "my-python-app" 是项目标识符，可以自定义

--data-dir 指定记忆文件存储位置，使用 ${workspaceFolder} 变量（指向项目根目录）

