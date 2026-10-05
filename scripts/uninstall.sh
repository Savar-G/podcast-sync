#!/bin/zsh
# Stop the helper and remove the login item. Your Podcasts library is never changed.
LABEL="io.github.podcast-sync.helper"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed the helper."
echo "Optional cleanup:"
echo "  rm -rf \"$HOME/Library/Application Support/podcast-sync\"   # match cache, podcasts-remote"
echo "  rm -rf \"$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/podcast-sync\"   # iPhone link"
echo "Then remove the Chrome extension and the Resume Podcast shortcut."
