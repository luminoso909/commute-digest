#!/bin/bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
mode="${1:-}"
if [[ $(uname -s) != Darwin || $(sw_vers -productVersion | cut -d. -f1) -lt 14 ]]; then
    echo '小组件需要 macOS 14 或更高版本。' >&2
    exit 1
fi
if [[ $# -gt 1 || ( -n "$mode" && "$mode" != --package ) ]]; then
    echo '双击安装；从源码生成分发包可运行「安装小组件.command --package」。' >&2
    exit 1
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/commute-widget.XXXXXX")"
stage=''
lock=''
dest=''
installed=false
cleanup() {
    local keep_previous=false
    if [[ -n "$stage" ]]; then
        if [[ -d "$stage/previous.app" && "$installed" != true ]]; then
            if { [[ ! -e "$dest" ]] || mv "$dest" "$stage/failed.app"; } && mv "$stage/previous.app" "$dest"; then
                pluginkit -a "$dest/$widget" >/dev/null 2>&1 || true
                echo "已恢复上一次安装的应用。" >&2
            else
                keep_previous=true
                echo "旧版本保存在 $stage/previous.app" >&2
            fi
        fi
        if [[ "$keep_previous" != true ]]; then rm -rf "$stage"; fi
    fi
    if [[ -n "$lock" ]]; then rm -f "$lock"; fi
    rm -rf "$work"
}
trap cleanup EXIT

bundle="$here/CommuteDigest.app"
widget='Contents/PlugIns/CommuteDigestWidget.appex'
identifier() { /usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$1/Contents/Info.plist"; }
if [[ "$mode" == --package || ! -d "$bundle" ]]; then
    if [[ ! -d "$here/CommuteDigest.xcodeproj" ]] || ! /usr/bin/xcodebuild -version >/dev/null 2>&1; then
        echo '请解压完整的安装包，或安装并选用完整版 Xcode 后从源码运行。' >&2
        exit 1
    fi
    echo '正在构建通勤日报小组件…'
    /usr/bin/xcodebuild -quiet -project "$here/CommuteDigest.xcodeproj" -scheme CommuteDigest \
        -configuration Release -destination 'generic/platform=macOS' -derivedDataPath "$work/build" \
        ARCHS='arm64 x86_64' ONLY_ACTIVE_ARCH=NO build
    bundle="$work/build/Build/Products/Release/CommuteDigest.app"
fi
if [[ "$(identifier "$bundle")" != local.commutedigest.reader || \
      "$(identifier "$bundle/$widget")" != local.commutedigest.reader.widget ]]; then
    echo '安装包缺少正确的通勤日报应用或小组件。' >&2
    exit 1
fi
/usr/bin/codesign --verify --deep --strict "$bundle"

if [[ "$mode" == --package ]]; then
    folder="$work/通勤日报小组件"
    mkdir -p "$folder" "$here/../dist"
    ditto "$bundle" "$folder/CommuteDigest.app"
    cp -p "$0" "$folder/安装小组件.command"
    ditto -c -k --norsrc --noextattr --noqtn --noacl --keepParent "$folder" "$work/package.zip"
    mv -f "$work/package.zip" "$here/../dist/CommuteDigest-macOS.zip"
    echo "已生成 $here/../dist/CommuteDigest-macOS.zip"
    exit 0
fi

apps="${COMMUTE_WIDGET_INSTALL_DIR:-$HOME/Applications}"
mkdir -p "$apps"
dest="$apps/CommuteDigest.app"
if ! /usr/bin/shlock -p "$$" -f "$apps/.commute-widget-install.lock"; then
    echo '另一份安装程序正在运行。' >&2
    exit 1
fi
lock="$apps/.commute-widget-install.lock"
if [[ -L "$dest" || ( -e "$dest" && "$(identifier "$dest" 2>/dev/null)" != local.commutedigest.reader ) ]]; then
    echo "安装位置已被其他文件占用：$dest" >&2
    exit 1
fi
stage="$(mktemp -d "$apps/.commute-widget.XXXXXX")"
ditto "$bundle" "$stage/next.app"
/usr/bin/codesign --verify --deep --strict "$stage/next.app"
if [[ -d "$dest" ]]; then
    # Quit only copies running this exact installed executable before replacing it.
    while read -r pid; do
        if [[ -n "$pid" ]]; then kill -TERM "$pid" 2>/dev/null || true; fi
    done < <(/usr/sbin/lsof -t -a -d txt "$dest/Contents/MacOS/CommuteDigest" 2>/dev/null || true)
    mv "$dest" "$stage/previous.app"
fi
mv "$stage/next.app" "$dest"
pluginkit -a "$dest/$widget"
open "$dest"
installed=true
echo "已安装到 $dest"
echo '右键桌面，选择「编辑小组件」，搜索「通勤日报」并添加。'
