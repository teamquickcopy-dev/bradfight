# Shot list: fighter01

Template animations: `kfm720.air`. You need **240 sprites in 50 groups**, plus portraits 9000,0 (small) and 9000,1 (big) in `portraits/9000_portraits/`.

Save each frame as `<image number>.png`, zero-padded (`0000.png`, `0010.png`), in its folder.
Build with `NUMBER_FROM=filename`. Rows marked *gaps* only build correctly that way.

| Group | Folder | Images | Count | Used by actions |
|---:|---|---|---:|---|
| 0 | `0_standing_animation` | 0, 1, 2, 3, 4, 5 | 6 | 0 Standing Animation, 190 INTRO |
| 5 | `5_turning` | 0 | 1 | 5 Turning |
| 6 | `6_crouch_turning` | 0 | 1 | 6 Crouch Turning |
| 11 | `11_crouching_animation` | 0, 1, 2 | 3 | 10 Stand to crouch, 11 Crouching animation, 12 Crouch to Stand, 40 Jump start frame, +5 more |
| 20 | `20_walking_forward` | 0–15 | 16 | 20 Walking Forward |
| 21 | `21_walking_back` | 0–15 | 16 | 21 Walking Back |
| 41 | `41_starting_jumping_up` | 0, 1, 2, 3, 4, 5, 6, 7, 8 | 9 | 41 Starting Jumping up, 42 Starting Jumping forwards, 43 Starting Jumping backwards, 105 Hop backwards, +6 more |
| 42 | `42_starting_jumping_forwards` | 0, 1, 3, 4, 5 *gaps* | 5 | 42 Starting Jumping forwards, 5040 Air Recover, 5200 Fall-recovery near ground, 5210 Fall-recovery in mid-air |
| 43 | `43_starting_jumping_backwards` | 0, 1 | 2 | 43 Starting Jumping backwards, 105 Hop backwards |
| 100 | `100_run_forwards` | 0, 1, 2, 3, 4, 5 | 6 | 100 Run forwards |
| 120 | `120_guardstart_stand` | 0, 1, 2 | 3 | 120 GUARDSTART (stand), 130 GUARD (stand), 140 GUARDEND (stand), 150 Hit back while guarding (stand) |
| 121 | `121_guardstart_crouch` | 0 | 1 | 121 GUARDSTART (crouch), 131 GUARD (crouch), 141 GUARDEND (crouch), 151 Hit back while guarding (crouch) |
| 122 | `122_guardstart_air` | 0 | 1 | 122 GUARDSTART (air), 132 GUARD (air), 142 GUARDEND (air), 152 Hit back while guarding (air) |
| 181 | `181_win` | 0, 1, 2, 3, 4, 5, 6 | 7 | 181 Win, 195 Taunt |
| 191 | `191_intro_wood_projectile` | 0, 1, 2, 3 | 4 | 191 INTRO -- WOOD (projectile) |
| 192 | `192_intro_woodbreak_projectile` | 0, 1 | 2 | 192 INTRO -- WOODBREAK (projectile) |
| 200 | `200_stand_light_punch` | 0, 1, 2 | 3 | 181 Win, 190 INTRO, 195 Taunt, 200 Stand Light Punch, +21 more |
| 210 | `210_stand_strong_punch` | 0, 1, 2, 3, 4, 5 | 6 | 210 Stand Strong Punch |
| 230 | `230_standing_light_kick` | 0, 1, 2 | 3 | 230 Standing Light Kick |
| 240 | `240_standing_strong_kick` | 0, 1, 2, 3, 4, 5 | 6 | 190 INTRO, 240 Standing Strong Kick |
| 400 | `400_crouching_light_punch` | 0, 1 | 2 | 400 Crouching light punch |
| 410 | `410_crouching_strong_punch` | 0, 1, 2, 3, 4 | 5 | 410 Crouching strong punch |
| 430 | `430_crouching_light_kick` | 0, 1, 2, 3 | 4 | 430 Crouching Light Kick |
| 440 | `440_crouching_strong_kick` | 0, 1, 2, 3, 4, 5, 6 | 7 | 440 Crouching Strong Kick |
| 600 | `600_jump_light_punch` | 0, 1 | 2 | 600 Jump light punch |
| 610 | `610_jump_strong_punch` | 0, 1, 2, 3 | 4 | 610 Jump strong punch |
| 630 | `630_jump_light_kick` | 1, 2 *gaps* | 2 | 630 Jump light kick |
| 640 | `640_jump_strong_kick` | 0, 1, 2 | 3 | 630 Jump light kick, 640 Jump strong kick |
| 800 | `800_kung_fu_throw_attempt` | 0–12 | 13 | 800 Kung Fu Throw Attempt, 810 Kung Fu Throw |
| 1000 | `1000_light_kung_fu_palm` | 0, 1, 2, 3, 4, 5, 6, 7 | 8 | 1000 Light Kung Fu Palm, 1010 Strong Kung Fu Palm, 1020 Fast Kung Fu Palm, 3000 Triple Kung Fu Palm |
| 1050 | `1050_light_kung_fu_knee_move_forwards` | 0, 1, 2, 3, 4, 5, 6, 7 | 8 | 1050 Light Kung Fu Knee - move forwards, 1051 Light Kung Fu Knee - jump, 1060 Strong Kung Fu Knee - move forwards, 1061 Strong Kung Fu Knee - jump, +2 more |
| 1052 | `1052_light_kung_fu_knee_land` | 0, 1, 2, 3 | 4 | 1052 Light Kung Fu Knee - land |
| 1055 | `1055_light_kung_fu_knee_kick` | 0, 1, 2, 3, 4, 5, 6, 7 | 8 | 1055 Light Kung Fu Knee - kick |
| 1100 | `1100_light_kung_fu_upper` | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 | 10 | 1100 Light Kung Fu Upper, 1110 Strong Kung Fu Upper, 1120 Fast Kung Fu Upper, 3050 Smash Kung Fu Upper, +1 more |
| 1210 | `1210_strong_kung_fu_blow` | 0, 1, 2, 3, 4, 5, 6 | 7 | 1200 Light Kung Fu Blow, 1210 Strong Kung Fu Blow, 1220 Fast Kung Fu Blow |
| 1300 | `1300_kung_fu_blocking_high_attempt` | 0, 1, 2 | 3 | 1300 Kung Fu Blocking High (attempt), 1310 Kung Fu Blocking High (successful block) |
| 1320 | `1320_kung_fu_blocking_low_attempt` | 0, 1, 2 | 3 | 1320 Kung Fu Blocking Low (attempt), 1330 Kung Fu Blocking Low (successful block) |
| 1340 | `1340_kung_fu_blocking_air_attempt` | 0, 1 | 2 | 1340 Kung Fu Blocking Air (attempt), 1350 Kung Fu Blocking Air (successful block) |
| 1400 | `1400_light_kung_fu_zankou` | 0, 1, 2, 3, 4, 5, 6, 7 | 8 | 1400 Light Kung Fu Zankou, 1410 Strong Kung Fu Zankou, 1420 Far Kung Fu Zankou |
| 5000 | `5000_stand_air_hit_high_light` | 0, 10, 11, 20, 21 *gaps* | 5 | 170 Lose, 820 Thrown by Kung Fu Throw (animation for opponent), 5000 Stand/Air Hit high (light), 5001 Stand/Air Hit high (medium), +4 more |
| 5010 | `5010_stand_air_hit_low_light` | 0, 10, 20, 21 *gaps* | 4 | 170 Lose, 820 Thrown by Kung Fu Throw (animation for opponent), 1025 Hit by Fast Kung Fu Palm, 1027 Hit by Fast Kung Fu Palm - hit wall, +7 more |
| 5011 | `5011_thrown_by_kung_fu_throw_animation_for_op` | 0 | 1 | 820 Thrown by Kung Fu Throw (animation for opponent) |
| 5020 | `5020_crouch_hit_light` | 0, 10, 20 *gaps* | 3 | 5020 Crouch Hit (light), 5021 Crouch Hit (medium), 5022 Crouch Hit (hard), 5025 Crouch Recover (light), +2 more |
| 5030 | `5030_stand_air_hit_back` | 0, 10, 30, 31, 40, 50 *gaps* | 6 | 820 Thrown by Kung Fu Throw (animation for opponent), 1025 Hit by Fast Kung Fu Palm, 5030 Stand/Air Hit back, 5035 Stand/Air Hit transition, +5 more |
| 5031 | `5031_thrown_by_kung_fu_throw_animation_for_op` | 0 | 1 | 820 Thrown by Kung Fu Throw (animation for opponent) |
| 5040 | `5040_liedown_hit_stay_down` | 0, 1, 10, 11, 20 *gaps* | 5 | 5080 LieDown Hit (stay down), 5100 Hit ground from fall, 5101 Hit ground from fall (up-type), 5110 LieDown, +3 more |
| 5060 | `5060_air_fall_up_type_going_up` | 0, 1, 10 *gaps* | 3 | 5051 Air Fall (up-type, going up), 5061 Air Fall (up-type, coming down) |
| 5061 | `5061_thrown_by_kung_fu_throw_animation_for_op` | 0 | 1 | 820 Thrown by Kung Fu Throw (animation for opponent) |
| 5071 | `5071_thrown_by_kung_fu_throw_animation_for_op` | 0, 10, 20 *gaps* | 3 | 820 Thrown by Kung Fu Throw (animation for opponent) |
| 5300 | `5300_dizzy` | 0, 1, 2, 3 | 4 | 170 Lose, 5300 Dizzy |
