#!/bin/bash
# creates a dir for each movie and puts it in that dir
# subtitle files will also have their own dir so deal with those manually first then run this script
# save this script in jellyfin media's dir

# run `chmod -x organize_movies.sh first` time
# then run ./organize_movies.sh

cd ./movies || exit 1 # change path as needed

for f in *; do
  # skip if it's already a directory
  if [ -d "$f" ]; then
    continue
  fi

  # strip the file extension to get the folder name
  name="${f%.*}"

  mkdir -p "$name"
  mv "$f" "$name/"
done
