#!/bin/bash

# Implementation note.
#
# Implementation note.
# Implementation note.
# Implementation note.

set -e

echo "🥋 Design Kungfu Skill 安装程序"
echo "================================"
echo ""

# Implementation note.
OS="$(uname -s)"
case "${OS}" in
    Linux*)     MACHINE=Linux;;
    Darwin*)    MACHINE=Mac;;
    CYGWIN*)    MACHINE=Cygwin;;
    MINGW*)     MACHINE=MinGw;;
    *)          MACHINE="UNKNOWN:${OS}"
esac

echo "🖥️  检测到系统: ${MACHINE}"
echo ""

# Implementation note.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
AGENTS_DIR="$HOME/.agents"
SKILLS_DIR="$AGENTS_DIR/skills"
TARGET_DIR="$SKILLS_DIR/design-kungfu"

echo "📁 安装目录:"
echo "   源目录: $SCRIPT_DIR"
echo "   目标目录: $TARGET_DIR"
echo ""

# Implementation note.
echo "请选择安装方式:"
echo "1) 全局安装 (推荐) - 复制到 ~/.agents/skills/design-kungfu"
echo "2) 创建符号链接 - 从当前位置链接到 ~/.agents/skills/design-kungfu"
echo "3) 退出"
echo ""
read -p "请输入选项 (1-3): " choice

case $choice in
    1)
        echo ""
        echo "📦 开始全局安装..."

        # Implementation note.
        mkdir -p "$SKILLS_DIR"

        # Implementation note.
        if [ -d "$TARGET_DIR" ]; then
            BACKUP_DIR="${TARGET_DIR}.backup.$(date +%Y%m%d%H%M%S)"
            echo "⚠️  发现已存在的安装，正在备份到: $BACKUP_DIR"
            mv "$TARGET_DIR" "$BACKUP_DIR"
        fi

        # Implementation note.
        echo "📋 复制文件..."
        cp -r "$SCRIPT_DIR" "$TARGET_DIR"

        # Implementation note.
        mkdir -p "$CLAUDE_DIR/skills"
        ln -sf "$TARGET_DIR" "$CLAUDE_DIR/skills/design-kungfu"

        echo ""
        echo "✅ 安装完成！"
        ;;

    2)
        echo ""
        echo "🔗 创建符号链接..."

        # Implementation note.
        mkdir -p "$SKILLS_DIR"

        # Implementation note.
        if [ -L "$TARGET_DIR" ]; then
            echo "⚠️  发现已存在的符号链接，正在删除..."
            rm "$TARGET_DIR"
        elif [ -d "$TARGET_DIR" ]; then
            BACKUP_DIR="${TARGET_DIR}.backup.$(date +%Y%m%d%H%M%S)"
            echo "⚠️  发现已存在的目录，正在备份到: $BACKUP_DIR"
            mv "$TARGET_DIR" "$BACKUP_DIR"
        fi

        # Implementation note.
        ln -s "$SCRIPT_DIR" "$TARGET_DIR"

        # Implementation note.
        mkdir -p "$CLAUDE_DIR/skills"
        ln -sf "$TARGET_DIR" "$CLAUDE_DIR/skills/design-kungfu"

        echo ""
        echo "✅ 符号链接创建完成！"
        ;;

    3)
        echo "👋 退出安装"
        exit 0
        ;;

    *)
        echo "❌ 无效的选项"
        exit 1
        ;;
esac

echo ""
echo "📊 安装摘要:"
echo "   Skill 目录: $TARGET_DIR"
echo "   符号链接: $CLAUDE_DIR/skills/design-kungfu"
echo "   风格数量: $(ls "$SCRIPT_DIR/styles" | wc -l) 个"
echo ""

# Implementation note.
if [ -f "$TARGET_DIR/SKILL.md" ] && [ -d "$TARGET_DIR/styles" ]; then
    echo "✅ 安装验证成功！"
    echo ""
    echo "🎉 Design Kungfu Skill 已准备就绪！"
    echo ""
    echo "📖 使用方法:"
    echo "   在 Claude Code 中直接使用，或输入 /design-kungfu 触发"
else
    echo "❌ 安装验证失败，请检查错误信息"
    exit 1
fi
