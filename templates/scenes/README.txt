SCENE TEMPLATES
===============

The automation loop needs to know which screen it is looking at. Each file in
this folder is a small crop of a distinctive, static part of a screen:

    victory.png       the "Victory" banner / result screen
    reward.png        the reward / card-pick screen
    battle_start.png  the battle-start or "Start Battle" button area

Names are configured in config.json -> "scenes". You can add more scenes there
and map them to click actions in the "clicks" section.

WHY THESE ARE NOT INCLUDED
--------------------------
They must come from the real game at the real window size. A template captured
at a different resolution, UI scale or language will not match.

HOW TO CAPTURE ONE
------------------
1. Put the game in windowed 1920x1080, get to the screen you want.
2. Find a small region of that screen that is always identical (a title, a
   button, a corner of the panel). Pick x1,y1,x2,y2 in game coordinates.
3. Run:

     python currency_wars_app-v9.py --capture-scene victory --scene-roi 820,300,1100,360

   Without --scene-roi the whole frame is saved, which usually matches too
   loosely; prefer a crop.
4. A full frame is also written to captures/ so you can verify what the bot
   saw. Check both images before trusting them.
5. Repeat for reward and battle_start.
6. Run:

     python currency_wars_app-v9.py --self-test

   and confirm the scene section reports PASS.

TUNING
------
Matching threshold is 0.84 in SceneDetector.detect. If a template matches on
the wrong screen, crop it tighter rather than lowering the threshold.
