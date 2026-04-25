import sys
from bot.youtube import extract_info, get_quality_options
info = extract_info("https://www.youtube.com/watch?v=BaW_C-GQmHw")
opts = get_quality_options(info)
for o in opts:
    print(o)
