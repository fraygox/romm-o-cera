# The app's file set: what runs on the box, nothing else (no tests, tools or
# screenshots). Sourced by tools/build-release.sh (the release zip) and
# deploy.sh (a developer's deploy), so both ship the same files.
#   . tools/app-files.sh; copy_app_files <repo> <dest>     # dest = a new rommbrowser/ folder
copy_app_files() {
    mkdir -p "$2/romm" "$2/rbui/screens" "$2/rbui/widgets" "$2/fonts"
    for f in rommbrowser.pygame rommbrowser.pygame.keys rommlib.py rb_finalize.py rb_chd.py \
             rb_chd_plan.py rb_dl.py rb_repair.py icon.png LICENSE; do
        cp "$1/$f" "$2/" || return 1
    done
    cp "$1"/romm/*.py "$2/romm/" || return 1
    cp "$1"/rbui/*.py "$2/rbui/" || return 1
    cp "$1"/rbui/screens/*.py "$2/rbui/screens/" || return 1
    cp "$1"/rbui/widgets/*.py "$2/rbui/widgets/" || return 1
    cp "$1"/fonts/*.ttf "$1"/fonts/OFL-Rubik.txt "$2/fonts/" || return 1
}
