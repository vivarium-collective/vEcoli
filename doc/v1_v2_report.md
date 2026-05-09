# vEcoli v1 vs v2 — comparison_10s_16g_v1_aws vs comparison_10s_16g_v2_aws_listener_fix

_Generated from latest workflow runs by `runscripts/v1_v2_report.py`._

## Bulk parity matrix

`=` = bit-identical bulk vector at every common timestep. `Δ@<t>` = first divergence timestep. `—` = missing data.

| seed \\ gen | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 1 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 2 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 3 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 4 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 5 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 6 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 7 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 8 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |
| 9 | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = | = |

**All 160 cells bit-identical.**

_Source: `out/parity_matrix__comparison_10s_16g_v1_aws__comparison_10s_16g_v2_aws_listener_fix.tsv`._

## Cell cycle / division times

| Seed | Gen | V1 div_time | V2 div_time | V1 cycle | V2 cycle | Δ% |
|---|---|---|---|---|---|---|
| 0 | 1 | 2526 | 2526 | 2526 | 2526 | +0.0% |
| 0 | 2 | 5276 | 5276 | 2750 | 2750 | +0.0% |
| 0 | 3 | 8279 | 8279 | 3003 | 3003 | +0.0% |
| 0 | 4 | 11424 | 11424 | 3145 | 3145 | +0.0% |
| 0 | 5 | 14643 | 14643 | 3219 | 3219 | +0.0% |
| 0 | 6 | 17532 | 17532 | 2889 | 2889 | +0.0% |
| 0 | 7 | 20518 | 20518 | 2986 | 2986 | +0.0% |
| 0 | 8 | 23350 | 23350 | 2832 | 2832 | +0.0% |
| 0 | 9 | 26480 | 26480 | 3130 | 3130 | +0.0% |
| 0 | 10 | 29500 | 29500 | 3020 | 3020 | +0.0% |
| 0 | 11 | 32815 | 32815 | 3315 | 3315 | +0.0% |
| 0 | 12 | 36310 | 36310 | 3495 | 3495 | +0.0% |
| 0 | 13 | 39749 | 39749 | 3439 | 3439 | +0.0% |
| 0 | 14 | 43136 | 43136 | 3387 | 3387 | +0.0% |
| 0 | 15 | 45988 | 45988 | 2852 | 2852 | +0.0% |
| 0 | 16 | 48845 | 48845 | 2857 | 2857 | +0.0% |
| 1 | 1 | 2573 | 2573 | 2573 | 2573 | +0.0% |
| 1 | 2 | 5564 | 5564 | 2991 | 2991 | +0.0% |
| 1 | 3 | 8713 | 8713 | 3149 | 3149 | +0.0% |
| 1 | 4 | 11387 | 11387 | 2674 | 2674 | +0.0% |
| 1 | 5 | 14234 | 14234 | 2847 | 2847 | +0.0% |
| 1 | 6 | 17157 | 17157 | 2923 | 2923 | +0.0% |
| 1 | 7 | 20132 | 20132 | 2975 | 2975 | +0.0% |
| 1 | 8 | 23360 | 23360 | 3228 | 3228 | +0.0% |
| 1 | 9 | 26515 | 26515 | 3155 | 3155 | +0.0% |
| 1 | 10 | 29846 | 29846 | 3331 | 3331 | +0.0% |
| 1 | 11 | 33109 | 33109 | 3263 | 3263 | +0.0% |
| 1 | 12 | 36344 | 36344 | 3235 | 3235 | +0.0% |
| 1 | 13 | 39675 | 39675 | 3331 | 3331 | +0.0% |
| 1 | 14 | 42927 | 42927 | 3252 | 3252 | +0.0% |
| 1 | 15 | 45956 | 45956 | 3029 | 3029 | +0.0% |
| 1 | 16 | 48628 | 48628 | 2672 | 2672 | +0.0% |
| 2 | 1 | 3309 | 3309 | 3309 | 3309 | +0.0% |
| 2 | 2 | 6031 | 6031 | 2722 | 2722 | +0.0% |
| 2 | 3 | 8890 | 8890 | 2859 | 2859 | +0.0% |
| 2 | 4 | 11849 | 11849 | 2959 | 2959 | +0.0% |
| 2 | 5 | 14894 | 14894 | 3045 | 3045 | +0.0% |
| 2 | 6 | 17579 | 17579 | 2685 | 2685 | +0.0% |
| 2 | 7 | 20076 | 20076 | 2497 | 2497 | +0.0% |
| 2 | 8 | 22673 | 22673 | 2597 | 2597 | +0.0% |
| 2 | 9 | 25517 | 25517 | 2844 | 2844 | +0.0% |
| 2 | 10 | 28377 | 28377 | 2860 | 2860 | +0.0% |
| 2 | 11 | 30910 | 30910 | 2533 | 2533 | +0.0% |
| 2 | 12 | 33687 | 33687 | 2777 | 2777 | +0.0% |
| 2 | 13 | 36508 | 36508 | 2821 | 2821 | +0.0% |
| 2 | 14 | 39519 | 39519 | 3011 | 3011 | +0.0% |
| 2 | 15 | 42367 | 42367 | 2848 | 2848 | +0.0% |
| 2 | 16 | 45388 | 45388 | 3021 | 3021 | +0.0% |
| 3 | 1 | 2519 | 2519 | 2519 | 2519 | +0.0% |
| 3 | 2 | 5413 | 5413 | 2894 | 2894 | +0.0% |
| 3 | 3 | 8615 | 8615 | 3202 | 3202 | +0.0% |
| 3 | 4 | 11518 | 11518 | 2903 | 2903 | +0.0% |
| 3 | 5 | 14382 | 14382 | 2864 | 2864 | +0.0% |
| 3 | 6 | 17774 | 17774 | 3392 | 3392 | +0.0% |
| 3 | 7 | 21149 | 21149 | 3375 | 3375 | +0.0% |
| 3 | 8 | 24180 | 24180 | 3031 | 3031 | +0.0% |
| 3 | 9 | 26991 | 26991 | 2811 | 2811 | +0.0% |
| 3 | 10 | 30068 | 30068 | 3077 | 3077 | +0.0% |
| 3 | 11 | 33273 | 33273 | 3205 | 3205 | +0.0% |
| 3 | 12 | 36420 | 36420 | 3147 | 3147 | +0.0% |
| 3 | 13 | 39253 | 39253 | 2833 | 2833 | +0.0% |
| 3 | 14 | 42268 | 42268 | 3015 | 3015 | +0.0% |
| 3 | 15 | 45645 | 45645 | 3377 | 3377 | +0.0% |
| 3 | 16 | 48983 | 48983 | 3338 | 3338 | +0.0% |
| 4 | 1 | 3127 | 3127 | 3127 | 3127 | +0.0% |
| 4 | 2 | 5962 | 5962 | 2835 | 2835 | +0.0% |
| 4 | 3 | 8982 | 8982 | 3020 | 3020 | +0.0% |
| 4 | 4 | 11863 | 11863 | 2881 | 2881 | +0.0% |
| 4 | 5 | 14741 | 14741 | 2878 | 2878 | +0.0% |
| 4 | 6 | 17895 | 17895 | 3154 | 3154 | +0.0% |
| 4 | 7 | 21149 | 21149 | 3254 | 3254 | +0.0% |
| 4 | 8 | 23971 | 23971 | 2822 | 2822 | +0.0% |
| 4 | 9 | 27035 | 27035 | 3064 | 3064 | +0.0% |
| 4 | 10 | 29959 | 29959 | 2924 | 2924 | +0.0% |
| 4 | 11 | 32861 | 32861 | 2902 | 2902 | +0.0% |
| 4 | 12 | 35920 | 35920 | 3059 | 3059 | +0.0% |
| 4 | 13 | 38913 | 38913 | 2993 | 2993 | +0.0% |
| 4 | 14 | 42011 | 42011 | 3098 | 3098 | +0.0% |
| 4 | 15 | 45026 | 45026 | 3015 | 3015 | +0.0% |
| 4 | 16 | 48007 | 48007 | 2981 | 2981 | +0.0% |
| 5 | 1 | 2980 | 2980 | 2980 | 2980 | +0.0% |
| 5 | 2 | 5801 | 5801 | 2821 | 2821 | +0.0% |
| 5 | 3 | 9053 | 9053 | 3252 | 3252 | +0.0% |
| 5 | 4 | 12850 | 12850 | 3797 | 3797 | +0.0% |
| 5 | 5 | 16374 | 16374 | 3524 | 3524 | +0.0% |
| 5 | 6 | 20008 | 20008 | 3634 | 3634 | +0.0% |
| 5 | 7 | 23411 | 23411 | 3403 | 3403 | +0.0% |
| 5 | 8 | 26742 | 26742 | 3331 | 3331 | +0.0% |
| 5 | 9 | 29727 | 29727 | 2985 | 2985 | +0.0% |
| 5 | 10 | 32052 | 32052 | 2325 | 2325 | +0.0% |
| 5 | 11 | 34370 | 34370 | 2318 | 2318 | +0.0% |
| 5 | 12 | 37104 | 37104 | 2734 | 2734 | +0.0% |
| 5 | 13 | 40290 | 40290 | 3186 | 3186 | +0.0% |
| 5 | 14 | 43943 | 43943 | 3653 | 3653 | +0.0% |
| 5 | 15 | 47562 | 47562 | 3619 | 3619 | +0.0% |
| 5 | 16 | 50799 | 50799 | 3237 | 3237 | +0.0% |
| 6 | 1 | 3267 | 3267 | 3267 | 3267 | +0.0% |
| 6 | 2 | 5987 | 5987 | 2720 | 2720 | +0.0% |
| 6 | 3 | 8665 | 8665 | 2678 | 2678 | +0.0% |
| 6 | 4 | 11659 | 11659 | 2994 | 2994 | +0.0% |
| 6 | 5 | 14645 | 14645 | 2986 | 2986 | +0.0% |
| 6 | 6 | 17677 | 17677 | 3032 | 3032 | +0.0% |
| 6 | 7 | 20840 | 20840 | 3163 | 3163 | +0.0% |
| 6 | 8 | 24483 | 24483 | 3643 | 3643 | +0.0% |
| 6 | 9 | 28167 | 28167 | 3684 | 3684 | +0.0% |
| 6 | 10 | 31117 | 31117 | 2950 | 2950 | +0.0% |
| 6 | 11 | 34606 | 34606 | 3489 | 3489 | +0.0% |
| 6 | 12 | 38529 | 38529 | 3923 | 3923 | +0.0% |
| 6 | 13 | 42692 | 42692 | 4163 | 4163 | +0.0% |
| 6 | 14 | 46281 | 46281 | 3589 | 3589 | +0.0% |
| 6 | 15 | 49685 | 49685 | 3404 | 3404 | +0.0% |
| 6 | 16 | 53201 | 53201 | 3516 | 3516 | +0.0% |
| 7 | 1 | 2552 | 2552 | 2552 | 2552 | +0.0% |
| 7 | 2 | 5325 | 5325 | 2773 | 2773 | +0.0% |
| 7 | 3 | 8102 | 8102 | 2777 | 2777 | +0.0% |
| 7 | 4 | 11000 | 11000 | 2898 | 2898 | +0.0% |
| 7 | 5 | 13933 | 13933 | 2933 | 2933 | +0.0% |
| 7 | 6 | 16960 | 16960 | 3027 | 3027 | +0.0% |
| 7 | 7 | 19935 | 19935 | 2975 | 2975 | +0.0% |
| 7 | 8 | 22846 | 22846 | 2911 | 2911 | +0.0% |
| 7 | 9 | 25929 | 25929 | 3083 | 3083 | +0.0% |
| 7 | 10 | 29085 | 29085 | 3156 | 3156 | +0.0% |
| 7 | 11 | 31979 | 31979 | 2894 | 2894 | +0.0% |
| 7 | 12 | 35285 | 35285 | 3306 | 3306 | +0.0% |
| 7 | 13 | 38679 | 38679 | 3394 | 3394 | +0.0% |
| 7 | 14 | 41578 | 41578 | 2899 | 2899 | +0.0% |
| 7 | 15 | 44626 | 44626 | 3048 | 3048 | +0.0% |
| 7 | 16 | 47955 | 47955 | 3329 | 3329 | +0.0% |
| 8 | 1 | 3112 | 3112 | 3112 | 3112 | +0.0% |
| 8 | 2 | 6052 | 6052 | 2940 | 2940 | +0.0% |
| 8 | 3 | 9057 | 9057 | 3005 | 3005 | +0.0% |
| 8 | 4 | 12317 | 12317 | 3260 | 3260 | +0.0% |
| 8 | 5 | 15828 | 15828 | 3511 | 3511 | +0.0% |
| 8 | 6 | 19011 | 19011 | 3183 | 3183 | +0.0% |
| 8 | 7 | 21853 | 21853 | 2842 | 2842 | +0.0% |
| 8 | 8 | 24708 | 24708 | 2855 | 2855 | +0.0% |
| 8 | 9 | 27795 | 27795 | 3087 | 3087 | +0.0% |
| 8 | 10 | 30667 | 30667 | 2872 | 2872 | +0.0% |
| 8 | 11 | 33737 | 33737 | 3070 | 3070 | +0.0% |
| 8 | 12 | 36895 | 36895 | 3158 | 3158 | +0.0% |
| 8 | 13 | 39992 | 39992 | 3097 | 3097 | +0.0% |
| 8 | 14 | 43074 | 43074 | 3082 | 3082 | +0.0% |
| 8 | 15 | 45749 | 45749 | 2675 | 2675 | +0.0% |
| 8 | 16 | 49000 | 49000 | 3251 | 3251 | +0.0% |
| 9 | 1 | 3145 | 3145 | 3145 | 3145 | +0.0% |
| 9 | 2 | 5821 | 5821 | 2676 | 2676 | +0.0% |
| 9 | 3 | 8356 | 8356 | 2535 | 2535 | +0.0% |
| 9 | 4 | 11322 | 11322 | 2966 | 2966 | +0.0% |
| 9 | 5 | 14690 | 14690 | 3368 | 3368 | +0.0% |
| 9 | 6 | 18055 | 18055 | 3365 | 3365 | +0.0% |
| 9 | 7 | 21105 | 21105 | 3050 | 3050 | +0.0% |
| 9 | 8 | 24399 | 24399 | 3294 | 3294 | +0.0% |
| 9 | 9 | 27677 | 27677 | 3278 | 3278 | +0.0% |
| 9 | 10 | 30514 | 30514 | 2837 | 2837 | +0.0% |
| 9 | 11 | 33482 | 33482 | 2968 | 2968 | +0.0% |
| 9 | 12 | 36922 | 36922 | 3440 | 3440 | +0.0% |
| 9 | 13 | 40364 | 40364 | 3442 | 3442 | +0.0% |
| 9 | 14 | 43564 | 43564 | 3200 | 3200 | +0.0% |
| 9 | 15 | 46967 | 46967 | 3403 | 3403 | +0.0% |
| 9 | 16 | 50601 | 50601 | 3634 | 3634 | +0.0% |

## Runtime per task (sum across instances)

| Sim | V1 wall (s) | V2 wall (s) | V1 s/tick | V2 s/tick | Δ wall % |
|---|---|---|---|---|---|
| seed 0 gen 1 | 410 | 420 | 0.162 | 0.166 | +2.5% |
| seed 0 gen 2 | 430 | 439 | 0.156 | 0.160 | +2.2% |
| seed 0 gen 3 | 450 | 470 | 0.150 | 0.156 | +4.5% |
| seed 0 gen 4 | 450 | 490 | 0.143 | 0.156 | +8.9% |
| seed 0 gen 5 | 490 | 500 | 0.152 | 0.155 | +2.0% |
| seed 0 gen 6 | 430 | 470 | 0.149 | 0.163 | +9.3% |
| seed 0 gen 7 | 440 | 470 | 0.147 | 0.157 | +6.8% |
| seed 0 gen 8 | 410 | 440 | 0.145 | 0.155 | +7.4% |
| seed 0 gen 9 | 460 | 480 | 0.147 | 0.153 | +4.3% |
| seed 0 gen 10 | 450 | 460 | 0.149 | 0.152 | +2.2% |
| seed 0 gen 11 | 480 | 490 | 0.145 | 0.148 | +2.1% |
| seed 0 gen 12 | 490 | 520 | 0.140 | 0.149 | +6.1% |
| seed 0 gen 13 | 500 | 520 | 0.145 | 0.151 | +4.0% |
| seed 0 gen 14 | 510 | 520 | 0.151 | 0.153 | +2.0% |
| seed 0 gen 15 | 430 | 450 | 0.151 | 0.158 | +4.7% |
| seed 0 gen 16 | 430 | 440 | 0.150 | 0.154 | +2.3% |
| seed 1 gen 1 | 410 | 420 | 0.159 | 0.163 | +2.5% |
| seed 1 gen 2 | 470 | 490 | 0.157 | 0.164 | +4.2% |
| seed 1 gen 3 | 490 | 520 | 0.156 | 0.165 | +6.1% |
| seed 1 gen 4 | 420 | 460 | 0.157 | 0.172 | +9.5% |
| seed 1 gen 5 | 430 | 470 | 0.151 | 0.165 | +9.3% |
| seed 1 gen 6 | 450 | 470 | 0.154 | 0.161 | +4.4% |
| seed 1 gen 7 | 430 | 450 | 0.144 | 0.151 | +4.7% |
| seed 1 gen 8 | 480 | 490 | 0.149 | 0.152 | +2.1% |
| seed 1 gen 9 | 450 | 470 | 0.143 | 0.149 | +4.5% |
| seed 1 gen 10 | 490 | 500 | 0.147 | 0.150 | +2.1% |
| seed 1 gen 11 | 470 | 490 | 0.144 | 0.150 | +4.2% |
| seed 1 gen 12 | 450 | 480 | 0.139 | 0.148 | +6.7% |
| seed 1 gen 13 | 490 | 500 | 0.147 | 0.150 | +2.0% |
| seed 1 gen 14 | 480 | 500 | 0.148 | 0.154 | +4.2% |
| seed 1 gen 15 | 480 | 500 | 0.158 | 0.165 | +4.2% |
| seed 1 gen 16 | 410 | 410 | 0.153 | 0.153 | -0.0% |
| seed 2 gen 1 | 660 | 659 | 0.199 | 0.199 | -0.0% |
| seed 2 gen 2 | 440 | 450 | 0.162 | 0.165 | +2.3% |
| seed 2 gen 3 | 440 | 450 | 0.154 | 0.157 | +2.3% |
| seed 2 gen 4 | 440 | 460 | 0.149 | 0.155 | +4.5% |
| seed 2 gen 5 | 480 | 500 | 0.158 | 0.164 | +4.2% |
| seed 2 gen 6 | 440 | 460 | 0.164 | 0.171 | +4.6% |
| seed 2 gen 7 | 410 | 420 | 0.164 | 0.168 | +2.4% |
| seed 2 gen 8 | 410 | 430 | 0.158 | 0.165 | +4.9% |
| seed 2 gen 9 | 450 | 460 | 0.158 | 0.162 | +2.2% |
| seed 2 gen 10 | 530 | 490 | 0.185 | 0.171 | -7.6% |
| seed 2 gen 11 | 410 | 420 | 0.162 | 0.166 | +2.4% |
| seed 2 gen 12 | 430 | 440 | 0.155 | 0.158 | +2.3% |
| seed 2 gen 13 | 430 | 450 | 0.152 | 0.159 | +4.7% |
| seed 2 gen 14 | 470 | 470 | 0.156 | 0.156 | -0.0% |
| seed 2 gen 15 | 430 | 440 | 0.151 | 0.154 | +2.3% |
| seed 2 gen 16 | 460 | 460 | 0.152 | 0.152 | +0.0% |
| seed 3 gen 1 | 410 | 420 | 0.163 | 0.167 | +2.5% |
| seed 3 gen 2 | 440 | 450 | 0.152 | 0.155 | +2.2% |
| seed 3 gen 3 | 470 | 520 | 0.147 | 0.162 | +10.6% |
| seed 3 gen 4 | 440 | 470 | 0.151 | 0.162 | +6.8% |
| seed 3 gen 5 | 400 | 440 | 0.140 | 0.154 | +10.0% |
| seed 3 gen 6 | 470 | 510 | 0.139 | 0.150 | +8.5% |
| seed 3 gen 7 | 500 | 510 | 0.148 | 0.151 | +2.0% |
| seed 3 gen 8 | 470 | 480 | 0.155 | 0.158 | +2.1% |
| seed 3 gen 9 | 460 | 450 | 0.164 | 0.160 | -2.2% |
| seed 3 gen 10 | 460 | 460 | 0.149 | 0.149 | -0.0% |
| seed 3 gen 11 | 470 | 490 | 0.147 | 0.153 | +4.2% |
| seed 3 gen 12 | 470 | 510 | 0.149 | 0.162 | +8.5% |
| seed 3 gen 13 | 420 | 450 | 0.148 | 0.159 | +7.1% |
| seed 3 gen 14 | 440 | 460 | 0.146 | 0.153 | +4.6% |
| seed 3 gen 15 | 480 | 510 | 0.142 | 0.151 | +6.3% |
| seed 3 gen 16 | 480 | 510 | 0.144 | 0.153 | +6.2% |
| seed 4 gen 1 | 489 | 520 | 0.157 | 0.166 | +6.2% |
| seed 4 gen 2 | 440 | 460 | 0.155 | 0.162 | +4.6% |
| seed 4 gen 3 | 450 | 500 | 0.149 | 0.165 | +11.1% |
| seed 4 gen 4 | 440 | 460 | 0.153 | 0.160 | +4.6% |
| seed 4 gen 5 | 430 | 450 | 0.149 | 0.156 | +4.7% |
| seed 4 gen 6 | 460 | 480 | 0.146 | 0.152 | +4.4% |
| seed 4 gen 7 | 470 | 510 | 0.144 | 0.157 | +8.5% |
| seed 4 gen 8 | 420 | 430 | 0.149 | 0.152 | +2.4% |
| seed 4 gen 9 | 490 | 470 | 0.160 | 0.153 | -4.1% |
| seed 4 gen 10 | 450 | 460 | 0.154 | 0.157 | +2.3% |
| seed 4 gen 11 | 440 | 450 | 0.152 | 0.155 | +2.2% |
| seed 4 gen 12 | 500 | 500 | 0.163 | 0.163 | +0.0% |
| seed 4 gen 13 | 490 | 490 | 0.164 | 0.164 | +0.0% |
| seed 4 gen 14 | 470 | 490 | 0.152 | 0.158 | +4.2% |
| seed 4 gen 15 | 450 | 480 | 0.149 | 0.159 | +6.7% |
| seed 4 gen 16 | 450 | 470 | 0.151 | 0.158 | +4.5% |
| seed 5 gen 1 | 470 | 490 | 0.158 | 0.164 | +4.3% |
| seed 5 gen 2 | 420 | 430 | 0.149 | 0.152 | +2.4% |
| seed 5 gen 3 | 460 | 470 | 0.141 | 0.144 | +2.2% |
| seed 5 gen 4 | 520 | 540 | 0.137 | 0.142 | +3.8% |
| seed 5 gen 5 | 490 | 510 | 0.139 | 0.145 | +4.1% |
| seed 5 gen 6 | 520 | 530 | 0.143 | 0.146 | +1.9% |
| seed 5 gen 7 | 480 | 510 | 0.141 | 0.150 | +6.3% |
| seed 5 gen 8 | 490 | 500 | 0.147 | 0.150 | +2.0% |
| seed 5 gen 9 | 500 | 500 | 0.167 | 0.167 | -0.0% |
| seed 5 gen 10 | 410 | 420 | 0.176 | 0.181 | +2.5% |
| seed 5 gen 11 | 370 | 390 | 0.160 | 0.168 | +5.4% |
| seed 5 gen 12 | 410 | 430 | 0.150 | 0.157 | +4.9% |
| seed 5 gen 13 | 440 | 460 | 0.138 | 0.144 | +4.6% |
| seed 5 gen 14 | 500 | 519 | 0.137 | 0.142 | +3.9% |
| seed 5 gen 15 | 520 | 530 | 0.144 | 0.146 | +1.9% |
| seed 5 gen 16 | 470 | 490 | 0.145 | 0.151 | +4.3% |
| seed 6 gen 1 | 520 | 600 | 0.159 | 0.184 | +15.4% |
| seed 6 gen 2 | 440 | 460 | 0.162 | 0.169 | +4.6% |
| seed 6 gen 3 | 420 | 430 | 0.157 | 0.160 | +2.4% |
| seed 6 gen 4 | 450 | 480 | 0.150 | 0.160 | +6.7% |
| seed 6 gen 5 | 450 | 470 | 0.151 | 0.157 | +4.5% |
| seed 6 gen 6 | 450 | 470 | 0.148 | 0.155 | +4.5% |
| seed 6 gen 7 | 450 | 470 | 0.142 | 0.149 | +4.5% |
| seed 6 gen 8 | 480 | 520 | 0.132 | 0.143 | +8.3% |
| seed 6 gen 9 | 540 | 560 | 0.147 | 0.152 | +3.7% |
| seed 6 gen 10 | 450 | 430 | 0.152 | 0.146 | -4.5% |
| seed 6 gen 11 | 480 | 490 | 0.138 | 0.140 | +2.1% |
| seed 6 gen 12 | 520 | 530 | 0.132 | 0.135 | +1.9% |
| seed 6 gen 13 | 560 | 590 | 0.134 | 0.142 | +5.4% |
| seed 6 gen 14 | 520 | 540 | 0.145 | 0.150 | +3.8% |
| seed 6 gen 15 | 480 | 510 | 0.141 | 0.150 | +6.3% |
| seed 6 gen 16 | 500 | 500 | 0.142 | 0.142 | +0.0% |
| seed 7 gen 1 | 420 | 440 | 0.164 | 0.172 | +4.8% |
| seed 7 gen 2 | 420 | 450 | 0.151 | 0.162 | +7.1% |
| seed 7 gen 3 | 430 | 460 | 0.155 | 0.166 | +7.0% |
| seed 7 gen 4 | 440 | 470 | 0.152 | 0.162 | +6.8% |
| seed 7 gen 5 | 440 | 450 | 0.150 | 0.153 | +2.3% |
| seed 7 gen 6 | 460 | 480 | 0.152 | 0.158 | +4.3% |
| seed 7 gen 7 | 460 | 470 | 0.154 | 0.158 | +2.2% |
| seed 7 gen 8 | 450 | 470 | 0.155 | 0.161 | +4.4% |
| seed 7 gen 9 | 460 | 470 | 0.149 | 0.152 | +2.2% |
| seed 7 gen 10 | 480 | 480 | 0.152 | 0.152 | -0.0% |
| seed 7 gen 11 | 430 | 440 | 0.148 | 0.152 | +2.3% |
| seed 7 gen 12 | 480 | 500 | 0.145 | 0.151 | +4.2% |
| seed 7 gen 13 | 510 | 510 | 0.150 | 0.150 | -0.0% |
| seed 7 gen 14 | 440 | 460 | 0.152 | 0.159 | +4.5% |
| seed 7 gen 15 | 440 | 460 | 0.144 | 0.151 | +4.6% |
| seed 7 gen 16 | 470 | 490 | 0.141 | 0.147 | +4.3% |
| seed 8 gen 1 | 620 | 590 | 0.199 | 0.189 | -4.8% |
| seed 8 gen 2 | 480 | 500 | 0.163 | 0.170 | +4.2% |
| seed 8 gen 3 | 450 | 470 | 0.150 | 0.156 | +4.5% |
| seed 8 gen 4 | 460 | 490 | 0.141 | 0.150 | +6.5% |
| seed 8 gen 5 | 510 | 540 | 0.145 | 0.154 | +5.9% |
| seed 8 gen 6 | 490 | 510 | 0.154 | 0.160 | +4.1% |
| seed 8 gen 7 | 440 | 450 | 0.155 | 0.158 | +2.3% |
| seed 8 gen 8 | 430 | 450 | 0.151 | 0.158 | +4.7% |
| seed 8 gen 9 | 470 | 480 | 0.152 | 0.155 | +2.1% |
| seed 8 gen 10 | 440 | 450 | 0.153 | 0.157 | +2.3% |
| seed 8 gen 11 | 460 | 470 | 0.150 | 0.153 | +2.2% |
| seed 8 gen 12 | 480 | 490 | 0.152 | 0.155 | +2.1% |
| seed 8 gen 13 | 480 | 470 | 0.155 | 0.152 | -2.1% |
| seed 8 gen 14 | 480 | 490 | 0.156 | 0.159 | +2.1% |
| seed 8 gen 15 | 400 | 420 | 0.149 | 0.157 | +5.0% |
| seed 8 gen 16 | 450 | 480 | 0.138 | 0.148 | +6.7% |
| seed 9 gen 1 | 500 | 650 | 0.159 | 0.207 | +30.0% |
| seed 9 gen 2 | 420 | 460 | 0.157 | 0.172 | +9.5% |
| seed 9 gen 3 | 380 | 420 | 0.150 | 0.166 | +10.5% |
| seed 9 gen 4 | 420 | 460 | 0.141 | 0.155 | +9.6% |
| seed 9 gen 5 | 470 | 510 | 0.139 | 0.151 | +8.5% |
| seed 9 gen 6 | 480 | 510 | 0.143 | 0.151 | +6.3% |
| seed 9 gen 7 | 450 | 460 | 0.147 | 0.151 | +2.2% |
| seed 9 gen 8 | 460 | 500 | 0.140 | 0.152 | +8.7% |
| seed 9 gen 9 | 490 | 510 | 0.149 | 0.156 | +4.1% |
| seed 9 gen 10 | 440 | 440 | 0.155 | 0.155 | -0.0% |
| seed 9 gen 11 | 420 | 440 | 0.141 | 0.148 | +4.8% |
| seed 9 gen 12 | 500 | 520 | 0.145 | 0.151 | +4.0% |
| seed 9 gen 13 | 500 | 510 | 0.145 | 0.148 | +2.0% |
| seed 9 gen 14 | 460 | 470 | 0.144 | 0.147 | +2.2% |
| seed 9 gen 15 | 500 | 500 | 0.147 | 0.147 | +0.0% |
| seed 9 gen 16 | 490 | 500 | 0.135 | 0.138 | +2.1% |
| **SIM TOTAL** | **73799** | **76760** | - | - | **+4.0%** |

## Analysis plots

### mass_fraction_summary — seed 0, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen1_v1.png) | ![mass_fraction_summary — seed 0, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen1_v2.png) |

### mass_fraction_summary — seed 0, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen2_v1.png) | ![mass_fraction_summary — seed 0, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen2_v2.png) |

### mass_fraction_summary — seed 0, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen3_v1.png) | ![mass_fraction_summary — seed 0, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen3_v2.png) |

### mass_fraction_summary — seed 0, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen4_v1.png) | ![mass_fraction_summary — seed 0, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen4_v2.png) |

### mass_fraction_summary — seed 0, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen5_v1.png) | ![mass_fraction_summary — seed 0, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen5_v2.png) |

### mass_fraction_summary — seed 0, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen6_v1.png) | ![mass_fraction_summary — seed 0, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen6_v2.png) |

### mass_fraction_summary — seed 0, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen7_v1.png) | ![mass_fraction_summary — seed 0, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen7_v2.png) |

### mass_fraction_summary — seed 0, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen8_v1.png) | ![mass_fraction_summary — seed 0, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen8_v2.png) |

### mass_fraction_summary — seed 0, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen9_v1.png) | ![mass_fraction_summary — seed 0, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen9_v2.png) |

### mass_fraction_summary — seed 0, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen10_v1.png) | ![mass_fraction_summary — seed 0, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen10_v2.png) |

### mass_fraction_summary — seed 0, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen11_v1.png) | ![mass_fraction_summary — seed 0, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen11_v2.png) |

### mass_fraction_summary — seed 0, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen12_v1.png) | ![mass_fraction_summary — seed 0, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen12_v2.png) |

### mass_fraction_summary — seed 0, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen13_v1.png) | ![mass_fraction_summary — seed 0, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen13_v2.png) |

### mass_fraction_summary — seed 0, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen14_v1.png) | ![mass_fraction_summary — seed 0, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen14_v2.png) |

### mass_fraction_summary — seed 0, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen15_v1.png) | ![mass_fraction_summary — seed 0, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen15_v2.png) |

### mass_fraction_summary — seed 0, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 0, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen16_v1.png) | ![mass_fraction_summary — seed 0, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed0_gen16_v2.png) |

### mass_fraction_summary — seed 1, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen1_v1.png) | ![mass_fraction_summary — seed 1, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen1_v2.png) |

### mass_fraction_summary — seed 1, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen2_v1.png) | ![mass_fraction_summary — seed 1, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen2_v2.png) |

### mass_fraction_summary — seed 1, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen3_v1.png) | ![mass_fraction_summary — seed 1, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen3_v2.png) |

### mass_fraction_summary — seed 1, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen4_v1.png) | ![mass_fraction_summary — seed 1, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen4_v2.png) |

### mass_fraction_summary — seed 1, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen5_v1.png) | ![mass_fraction_summary — seed 1, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen5_v2.png) |

### mass_fraction_summary — seed 1, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen6_v1.png) | ![mass_fraction_summary — seed 1, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen6_v2.png) |

### mass_fraction_summary — seed 1, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen7_v1.png) | ![mass_fraction_summary — seed 1, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen7_v2.png) |

### mass_fraction_summary — seed 1, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen8_v1.png) | ![mass_fraction_summary — seed 1, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen8_v2.png) |

### mass_fraction_summary — seed 1, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen9_v1.png) | ![mass_fraction_summary — seed 1, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen9_v2.png) |

### mass_fraction_summary — seed 1, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen10_v1.png) | ![mass_fraction_summary — seed 1, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen10_v2.png) |

### mass_fraction_summary — seed 1, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen11_v1.png) | ![mass_fraction_summary — seed 1, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen11_v2.png) |

### mass_fraction_summary — seed 1, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen12_v1.png) | ![mass_fraction_summary — seed 1, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen12_v2.png) |

### mass_fraction_summary — seed 1, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen13_v1.png) | ![mass_fraction_summary — seed 1, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen13_v2.png) |

### mass_fraction_summary — seed 1, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen14_v1.png) | ![mass_fraction_summary — seed 1, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen14_v2.png) |

### mass_fraction_summary — seed 1, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen15_v1.png) | ![mass_fraction_summary — seed 1, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen15_v2.png) |

### mass_fraction_summary — seed 1, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 1, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen16_v1.png) | ![mass_fraction_summary — seed 1, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed1_gen16_v2.png) |

### mass_fraction_summary — seed 2, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen1_v1.png) | ![mass_fraction_summary — seed 2, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen1_v2.png) |

### mass_fraction_summary — seed 2, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen2_v1.png) | ![mass_fraction_summary — seed 2, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen2_v2.png) |

### mass_fraction_summary — seed 2, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen3_v1.png) | ![mass_fraction_summary — seed 2, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen3_v2.png) |

### mass_fraction_summary — seed 2, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen4_v1.png) | ![mass_fraction_summary — seed 2, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen4_v2.png) |

### mass_fraction_summary — seed 2, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen5_v1.png) | ![mass_fraction_summary — seed 2, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen5_v2.png) |

### mass_fraction_summary — seed 2, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen6_v1.png) | ![mass_fraction_summary — seed 2, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen6_v2.png) |

### mass_fraction_summary — seed 2, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen7_v1.png) | ![mass_fraction_summary — seed 2, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen7_v2.png) |

### mass_fraction_summary — seed 2, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen8_v1.png) | ![mass_fraction_summary — seed 2, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen8_v2.png) |

### mass_fraction_summary — seed 2, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen9_v1.png) | ![mass_fraction_summary — seed 2, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen9_v2.png) |

### mass_fraction_summary — seed 2, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen10_v1.png) | ![mass_fraction_summary — seed 2, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen10_v2.png) |

### mass_fraction_summary — seed 2, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen11_v1.png) | ![mass_fraction_summary — seed 2, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen11_v2.png) |

### mass_fraction_summary — seed 2, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen12_v1.png) | ![mass_fraction_summary — seed 2, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen12_v2.png) |

### mass_fraction_summary — seed 2, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen13_v1.png) | ![mass_fraction_summary — seed 2, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen13_v2.png) |

### mass_fraction_summary — seed 2, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen14_v1.png) | ![mass_fraction_summary — seed 2, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen14_v2.png) |

### mass_fraction_summary — seed 2, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen15_v1.png) | ![mass_fraction_summary — seed 2, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen15_v2.png) |

### mass_fraction_summary — seed 2, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 2, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen16_v1.png) | ![mass_fraction_summary — seed 2, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed2_gen16_v2.png) |

### mass_fraction_summary — seed 3, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen1_v1.png) | ![mass_fraction_summary — seed 3, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen1_v2.png) |

### mass_fraction_summary — seed 3, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen2_v1.png) | ![mass_fraction_summary — seed 3, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen2_v2.png) |

### mass_fraction_summary — seed 3, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen3_v1.png) | ![mass_fraction_summary — seed 3, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen3_v2.png) |

### mass_fraction_summary — seed 3, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen4_v1.png) | ![mass_fraction_summary — seed 3, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen4_v2.png) |

### mass_fraction_summary — seed 3, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen5_v1.png) | ![mass_fraction_summary — seed 3, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen5_v2.png) |

### mass_fraction_summary — seed 3, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen6_v1.png) | ![mass_fraction_summary — seed 3, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen6_v2.png) |

### mass_fraction_summary — seed 3, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen7_v1.png) | ![mass_fraction_summary — seed 3, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen7_v2.png) |

### mass_fraction_summary — seed 3, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen8_v1.png) | ![mass_fraction_summary — seed 3, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen8_v2.png) |

### mass_fraction_summary — seed 3, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen9_v1.png) | ![mass_fraction_summary — seed 3, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen9_v2.png) |

### mass_fraction_summary — seed 3, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen10_v1.png) | ![mass_fraction_summary — seed 3, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen10_v2.png) |

### mass_fraction_summary — seed 3, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen11_v1.png) | ![mass_fraction_summary — seed 3, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen11_v2.png) |

### mass_fraction_summary — seed 3, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen12_v1.png) | ![mass_fraction_summary — seed 3, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen12_v2.png) |

### mass_fraction_summary — seed 3, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen13_v1.png) | ![mass_fraction_summary — seed 3, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen13_v2.png) |

### mass_fraction_summary — seed 3, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen14_v1.png) | ![mass_fraction_summary — seed 3, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen14_v2.png) |

### mass_fraction_summary — seed 3, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen15_v1.png) | ![mass_fraction_summary — seed 3, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen15_v2.png) |

### mass_fraction_summary — seed 3, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 3, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen16_v1.png) | ![mass_fraction_summary — seed 3, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed3_gen16_v2.png) |

### mass_fraction_summary — seed 4, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen1_v1.png) | ![mass_fraction_summary — seed 4, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen1_v2.png) |

### mass_fraction_summary — seed 4, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen2_v1.png) | ![mass_fraction_summary — seed 4, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen2_v2.png) |

### mass_fraction_summary — seed 4, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen3_v1.png) | ![mass_fraction_summary — seed 4, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen3_v2.png) |

### mass_fraction_summary — seed 4, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen4_v1.png) | ![mass_fraction_summary — seed 4, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen4_v2.png) |

### mass_fraction_summary — seed 4, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen5_v1.png) | ![mass_fraction_summary — seed 4, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen5_v2.png) |

### mass_fraction_summary — seed 4, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen6_v1.png) | ![mass_fraction_summary — seed 4, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen6_v2.png) |

### mass_fraction_summary — seed 4, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen7_v1.png) | ![mass_fraction_summary — seed 4, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen7_v2.png) |

### mass_fraction_summary — seed 4, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen8_v1.png) | ![mass_fraction_summary — seed 4, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen8_v2.png) |

### mass_fraction_summary — seed 4, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen9_v1.png) | ![mass_fraction_summary — seed 4, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen9_v2.png) |

### mass_fraction_summary — seed 4, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen10_v1.png) | ![mass_fraction_summary — seed 4, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen10_v2.png) |

### mass_fraction_summary — seed 4, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen11_v1.png) | ![mass_fraction_summary — seed 4, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen11_v2.png) |

### mass_fraction_summary — seed 4, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen12_v1.png) | ![mass_fraction_summary — seed 4, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen12_v2.png) |

### mass_fraction_summary — seed 4, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen13_v1.png) | ![mass_fraction_summary — seed 4, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen13_v2.png) |

### mass_fraction_summary — seed 4, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen14_v1.png) | ![mass_fraction_summary — seed 4, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen14_v2.png) |

### mass_fraction_summary — seed 4, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen15_v1.png) | ![mass_fraction_summary — seed 4, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen15_v2.png) |

### mass_fraction_summary — seed 4, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 4, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen16_v1.png) | ![mass_fraction_summary — seed 4, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed4_gen16_v2.png) |

### mass_fraction_summary — seed 5, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen1_v1.png) | ![mass_fraction_summary — seed 5, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen1_v2.png) |

### mass_fraction_summary — seed 5, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen2_v1.png) | ![mass_fraction_summary — seed 5, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen2_v2.png) |

### mass_fraction_summary — seed 5, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen3_v1.png) | ![mass_fraction_summary — seed 5, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen3_v2.png) |

### mass_fraction_summary — seed 5, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen4_v1.png) | ![mass_fraction_summary — seed 5, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen4_v2.png) |

### mass_fraction_summary — seed 5, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen5_v1.png) | ![mass_fraction_summary — seed 5, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen5_v2.png) |

### mass_fraction_summary — seed 5, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen6_v1.png) | ![mass_fraction_summary — seed 5, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen6_v2.png) |

### mass_fraction_summary — seed 5, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen7_v1.png) | ![mass_fraction_summary — seed 5, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen7_v2.png) |

### mass_fraction_summary — seed 5, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen8_v1.png) | ![mass_fraction_summary — seed 5, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen8_v2.png) |

### mass_fraction_summary — seed 5, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen9_v1.png) | ![mass_fraction_summary — seed 5, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen9_v2.png) |

### mass_fraction_summary — seed 5, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen10_v1.png) | ![mass_fraction_summary — seed 5, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen10_v2.png) |

### mass_fraction_summary — seed 5, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen11_v1.png) | ![mass_fraction_summary — seed 5, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen11_v2.png) |

### mass_fraction_summary — seed 5, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen12_v1.png) | ![mass_fraction_summary — seed 5, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen12_v2.png) |

### mass_fraction_summary — seed 5, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen13_v1.png) | ![mass_fraction_summary — seed 5, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen13_v2.png) |

### mass_fraction_summary — seed 5, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen14_v1.png) | ![mass_fraction_summary — seed 5, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen14_v2.png) |

### mass_fraction_summary — seed 5, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen15_v1.png) | ![mass_fraction_summary — seed 5, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen15_v2.png) |

### mass_fraction_summary — seed 5, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 5, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen16_v1.png) | ![mass_fraction_summary — seed 5, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed5_gen16_v2.png) |

### mass_fraction_summary — seed 6, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen1_v1.png) | ![mass_fraction_summary — seed 6, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen1_v2.png) |

### mass_fraction_summary — seed 6, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen2_v1.png) | ![mass_fraction_summary — seed 6, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen2_v2.png) |

### mass_fraction_summary — seed 6, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen3_v1.png) | ![mass_fraction_summary — seed 6, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen3_v2.png) |

### mass_fraction_summary — seed 6, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen4_v1.png) | ![mass_fraction_summary — seed 6, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen4_v2.png) |

### mass_fraction_summary — seed 6, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen5_v1.png) | ![mass_fraction_summary — seed 6, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen5_v2.png) |

### mass_fraction_summary — seed 6, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen6_v1.png) | ![mass_fraction_summary — seed 6, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen6_v2.png) |

### mass_fraction_summary — seed 6, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen7_v1.png) | ![mass_fraction_summary — seed 6, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen7_v2.png) |

### mass_fraction_summary — seed 6, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen8_v1.png) | ![mass_fraction_summary — seed 6, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen8_v2.png) |

### mass_fraction_summary — seed 6, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen9_v1.png) | ![mass_fraction_summary — seed 6, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen9_v2.png) |

### mass_fraction_summary — seed 6, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen10_v1.png) | ![mass_fraction_summary — seed 6, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen10_v2.png) |

### mass_fraction_summary — seed 6, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen11_v1.png) | ![mass_fraction_summary — seed 6, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen11_v2.png) |

### mass_fraction_summary — seed 6, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen12_v1.png) | ![mass_fraction_summary — seed 6, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen12_v2.png) |

### mass_fraction_summary — seed 6, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen13_v1.png) | ![mass_fraction_summary — seed 6, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen13_v2.png) |

### mass_fraction_summary — seed 6, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen14_v1.png) | ![mass_fraction_summary — seed 6, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen14_v2.png) |

### mass_fraction_summary — seed 6, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen15_v1.png) | ![mass_fraction_summary — seed 6, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen15_v2.png) |

### mass_fraction_summary — seed 6, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 6, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen16_v1.png) | ![mass_fraction_summary — seed 6, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed6_gen16_v2.png) |

### mass_fraction_summary — seed 7, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen1_v1.png) | ![mass_fraction_summary — seed 7, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen1_v2.png) |

### mass_fraction_summary — seed 7, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen2_v1.png) | ![mass_fraction_summary — seed 7, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen2_v2.png) |

### mass_fraction_summary — seed 7, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen3_v1.png) | ![mass_fraction_summary — seed 7, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen3_v2.png) |

### mass_fraction_summary — seed 7, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen4_v1.png) | ![mass_fraction_summary — seed 7, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen4_v2.png) |

### mass_fraction_summary — seed 7, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen5_v1.png) | ![mass_fraction_summary — seed 7, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen5_v2.png) |

### mass_fraction_summary — seed 7, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen6_v1.png) | ![mass_fraction_summary — seed 7, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen6_v2.png) |

### mass_fraction_summary — seed 7, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen7_v1.png) | ![mass_fraction_summary — seed 7, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen7_v2.png) |

### mass_fraction_summary — seed 7, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen8_v1.png) | ![mass_fraction_summary — seed 7, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen8_v2.png) |

### mass_fraction_summary — seed 7, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen9_v1.png) | ![mass_fraction_summary — seed 7, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen9_v2.png) |

### mass_fraction_summary — seed 7, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen10_v1.png) | ![mass_fraction_summary — seed 7, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen10_v2.png) |

### mass_fraction_summary — seed 7, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen11_v1.png) | ![mass_fraction_summary — seed 7, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen11_v2.png) |

### mass_fraction_summary — seed 7, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen12_v1.png) | ![mass_fraction_summary — seed 7, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen12_v2.png) |

### mass_fraction_summary — seed 7, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen13_v1.png) | ![mass_fraction_summary — seed 7, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen13_v2.png) |

### mass_fraction_summary — seed 7, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen14_v1.png) | ![mass_fraction_summary — seed 7, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen14_v2.png) |

### mass_fraction_summary — seed 7, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen15_v1.png) | ![mass_fraction_summary — seed 7, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen15_v2.png) |

### mass_fraction_summary — seed 7, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 7, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen16_v1.png) | ![mass_fraction_summary — seed 7, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed7_gen16_v2.png) |

### mass_fraction_summary — seed 8, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen1_v1.png) | ![mass_fraction_summary — seed 8, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen1_v2.png) |

### mass_fraction_summary — seed 8, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen2_v1.png) | ![mass_fraction_summary — seed 8, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen2_v2.png) |

### mass_fraction_summary — seed 8, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen3_v1.png) | ![mass_fraction_summary — seed 8, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen3_v2.png) |

### mass_fraction_summary — seed 8, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen4_v1.png) | ![mass_fraction_summary — seed 8, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen4_v2.png) |

### mass_fraction_summary — seed 8, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen5_v1.png) | ![mass_fraction_summary — seed 8, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen5_v2.png) |

### mass_fraction_summary — seed 8, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen6_v1.png) | ![mass_fraction_summary — seed 8, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen6_v2.png) |

### mass_fraction_summary — seed 8, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen7_v1.png) | ![mass_fraction_summary — seed 8, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen7_v2.png) |

### mass_fraction_summary — seed 8, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen8_v1.png) | ![mass_fraction_summary — seed 8, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen8_v2.png) |

### mass_fraction_summary — seed 8, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen9_v1.png) | ![mass_fraction_summary — seed 8, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen9_v2.png) |

### mass_fraction_summary — seed 8, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen10_v1.png) | ![mass_fraction_summary — seed 8, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen10_v2.png) |

### mass_fraction_summary — seed 8, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen11_v1.png) | ![mass_fraction_summary — seed 8, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen11_v2.png) |

### mass_fraction_summary — seed 8, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen12_v1.png) | ![mass_fraction_summary — seed 8, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen12_v2.png) |

### mass_fraction_summary — seed 8, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen13_v1.png) | ![mass_fraction_summary — seed 8, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen13_v2.png) |

### mass_fraction_summary — seed 8, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen14_v1.png) | ![mass_fraction_summary — seed 8, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen14_v2.png) |

### mass_fraction_summary — seed 8, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen15_v1.png) | ![mass_fraction_summary — seed 8, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen15_v2.png) |

### mass_fraction_summary — seed 8, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 8, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen16_v1.png) | ![mass_fraction_summary — seed 8, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed8_gen16_v2.png) |

### mass_fraction_summary — seed 9, gen 1

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen1_v1.png) | ![mass_fraction_summary — seed 9, gen 1](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen1_v2.png) |

### mass_fraction_summary — seed 9, gen 2

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen2_v1.png) | ![mass_fraction_summary — seed 9, gen 2](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen2_v2.png) |

### mass_fraction_summary — seed 9, gen 3

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen3_v1.png) | ![mass_fraction_summary — seed 9, gen 3](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen3_v2.png) |

### mass_fraction_summary — seed 9, gen 4

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen4_v1.png) | ![mass_fraction_summary — seed 9, gen 4](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen4_v2.png) |

### mass_fraction_summary — seed 9, gen 5

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen5_v1.png) | ![mass_fraction_summary — seed 9, gen 5](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen5_v2.png) |

### mass_fraction_summary — seed 9, gen 6

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen6_v1.png) | ![mass_fraction_summary — seed 9, gen 6](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen6_v2.png) |

### mass_fraction_summary — seed 9, gen 7

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen7_v1.png) | ![mass_fraction_summary — seed 9, gen 7](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen7_v2.png) |

### mass_fraction_summary — seed 9, gen 8

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen8_v1.png) | ![mass_fraction_summary — seed 9, gen 8](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen8_v2.png) |

### mass_fraction_summary — seed 9, gen 9

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen9_v1.png) | ![mass_fraction_summary — seed 9, gen 9](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen9_v2.png) |

### mass_fraction_summary — seed 9, gen 10

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen10_v1.png) | ![mass_fraction_summary — seed 9, gen 10](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen10_v2.png) |

### mass_fraction_summary — seed 9, gen 11

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen11_v1.png) | ![mass_fraction_summary — seed 9, gen 11](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen11_v2.png) |

### mass_fraction_summary — seed 9, gen 12

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen12_v1.png) | ![mass_fraction_summary — seed 9, gen 12](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen12_v2.png) |

### mass_fraction_summary — seed 9, gen 13

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen13_v1.png) | ![mass_fraction_summary — seed 9, gen 13](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen13_v2.png) |

### mass_fraction_summary — seed 9, gen 14

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen14_v1.png) | ![mass_fraction_summary — seed 9, gen 14](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen14_v2.png) |

### mass_fraction_summary — seed 9, gen 15

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen15_v1.png) | ![mass_fraction_summary — seed 9, gen 15](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen15_v2.png) |

### mass_fraction_summary — seed 9, gen 16

| V1 | V2 |
|---|---|
| ![mass_fraction_summary — seed 9, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen16_v1.png) | ![mass_fraction_summary — seed 9, gen 16](_static/v1_v2_report_assets/mass_fraction_summary__seed9_gen16_v2.png) |

### protein_counts_validation (multiseed)

| V1 | V2 |
|---|---|
| ![protein_counts_validation (multiseed)](_static/v1_v2_report_assets/protein_counts_validation_v1.png) | ![protein_counts_validation (multiseed)](_static/v1_v2_report_assets/protein_counts_validation_v2.png) |

### subgenerational_expression_table (multiseed)

**V1** ([full file](_static/v1_v2_report_assets/subgenerational_expression_table_v1.tsv))

| p_expressed | max_monomer_counts | max_mRNA_counts | cistron_idx | gene_name | cistron_name | protein_name |
|---|---|---|---|---|---|---|
| 0.99375 | 318 | 6 | 1 | EG10001 | EG10001_RNA | ALARACEBIOSYN-MONOMER |
| 0.975 | 376 | 4 | 2 | EG10002 | EG10002_RNA | MODB-MONOMER |
| 0.05625 | 1305 | 1 | 4 | EG10004 | EG10004_RNA | EG10004-MONOMER |
| 0.175 | 1368 | 2 | 6 | EG10007 | EG10007_RNA | HISM-MONOMER |
| 0.96875 | 231 | 4 | 8 | EG10011 | EG10011_RNA | EG10011-MONOMER |
| 0.125 | 951 | 1 | 10 | EG10013 | EG10013_RNA | EG10013-MONOMER |
| 0.08125 | 105 | 1 | 12 | EG10015 | EG10015_RNA | EG10015-MONOMER |
| 0.8 | 696 | 3 | 14 | EG10017 | EG10017_RNA | OPPSYN-MONOMER |
| 0.89375 | 170 | 4 | 16 | EG10020 | EG10020_RNA | CPXR-MONOMER |
| 0.9875 | 15637 | 7 | 21 | EG10025 | EG10025_RNA | E2P-MONOMER |

_… 2,706 more rows_

**V2** ([full file](_static/v1_v2_report_assets/subgenerational_expression_table_v2.tsv))

| p_expressed | max_monomer_counts | max_mRNA_counts | cistron_idx | gene_name | cistron_name | protein_name |
|---|---|---|---|---|---|---|
| 0.99375 | 318 | 6 | 1 | EG10001 | EG10001_RNA | ALARACEBIOSYN-MONOMER |
| 0.975 | 376 | 4 | 2 | EG10002 | EG10002_RNA | MODB-MONOMER |
| 0.05625 | 1305 | 1 | 4 | EG10004 | EG10004_RNA | EG10004-MONOMER |
| 0.175 | 1368 | 2 | 6 | EG10007 | EG10007_RNA | HISM-MONOMER |
| 0.96875 | 231 | 4 | 8 | EG10011 | EG10011_RNA | EG10011-MONOMER |
| 0.125 | 951 | 1 | 10 | EG10013 | EG10013_RNA | EG10013-MONOMER |
| 0.08125 | 105 | 1 | 12 | EG10015 | EG10015_RNA | EG10015-MONOMER |
| 0.8 | 696 | 3 | 14 | EG10017 | EG10017_RNA | OPPSYN-MONOMER |
| 0.89375 | 170 | 4 | 16 | EG10020 | EG10020_RNA | CPXR-MONOMER |
| 0.9875 | 15637 | 7 | 21 | EG10025 | EG10025_RNA | E2P-MONOMER |

_… 2,706 more rows_

### ecocyc_table (multiseed)

**V1** ([full file](_static/v1_v2_report_assets/ecocyc_table_v1.tsv))

| # Column descriptions: |
|---|
| # id | Object ID, according to EcoCyc |
| # flux-avg | A floating point number in mmol/g DCW/h units |
| # flux-std | A floating point number in mmol/g DCW/h units |
| id | flux-avg | flux-std |
| 1-ACYLGLYCEROL-3-P-ACYLTRANSFER-RXN | -2.775083597635423e-18 | 7.245596311876702e-17 |
| 1.1.1.127-RXN | 0.0 | 0.0 |
| 1.1.1.215-RXN | -4.8960644375656245e-9 | 3.467315485501154e-8 |
| 1.1.1.251-RXN | 0.0 | 0.0 |
| 1.1.1.271-RXN | -1.347046425451607e-12 | 7.074882377005504e-10 |
| 1.1.1.274-RXN | -4.8960644375656245e-9 | 3.467315485501154e-8 |

_… 2,815 more rows_

**V2** ([full file](_static/v1_v2_report_assets/ecocyc_table_v2.tsv))

| # Column descriptions: |
|---|
| # id | Object ID, according to EcoCyc |
| # protein-count-avg | A floating point number |
| # protein-count-std | A floating point number |
| # protein-concentration-avg | A floating point number in mM units |
| # protein-concentration-std | A floating point number in mM units |
| # relative-protein-count-to-protein-rna-counts | A floating point number |
| # relative-protein-mass-to-total-protein-mass | A floating point number |
| # relative-protein-mass-to-total-cell-dry-mass | A floating point number |
| # validation-count | A floating point number |
| id | protein-count-avg | protein-count-std | protein-concentration-avg | protein-concentration-std | relative-protein-count-to-protein-rna-counts | relative-protein-mass-to-total-protein-mass | relative-protein-mass-to-total-cell-dry-mass | validation-count |

_… 4,309 more rows_

