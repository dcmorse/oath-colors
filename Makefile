# Build every piece SVG into:
#   tts   : textured OBJ (plus .mtl/.png) for each pawn, for Tabletop Simulator
#   print : STL prism + black-only ink SVG for each pawn and warband
#   plates: for each pawn with a matching warband (same color and version),
#           one STL with both laid out for one print:
#           COLOR-pawn-VER.svg + COLOR-warband-VER.svg -> COLOR-plate-VER.stl
# Only rebuilds when an output is missing or older than its .svg
# (or than the converter script).
#
# svg_to_print.py writes NAME-ink.svg next to its inputs, and those match the
# same wildcards as the art, so every SVG list drops names ending in ink.svg
# before anything is built from it.

SVG_TO_TTS := ./svg_to_tts.py
TTS_FLAGS  := --width 1.8 --depth 0.9

# Size comes from the SVG itself (96 dpi user units); only depth is set here.
SVG_TO_PRINT        := ./svg_to_print.py
PAWN_PRINT_FLAGS    := --depth 15
WARBAND_PRINT_FLAGS := --depth 9

# One print job per pawn: the pawn plus its matching warbands.
STL_PLATE          := ./stl_plate.py
WARBANDS_PER_PLATE := 14
PLATE_GAP          := 5

not_ink = $(filter-out %ink.svg,$(1))

PAWN_SVGS    := $(call not_ink,$(wildcard *pawn-*.svg))
WARBAND_SVGS := $(call not_ink,$(wildcard *warband-*.svg))

OBJS         := $(PAWN_SVGS:.svg=.obj)
PAWN_STLS    := $(PAWN_SVGS:.svg=.stl)
WARBAND_STLS := $(WARBAND_SVGS:.svg=.stl)
STLS         := $(PAWN_STLS) $(WARBAND_STLS)
INKS         := $(STLS:.stl=-ink.svg)
# A plate for each pawn whose matching warband exists; other pawns get none.
PLATES       := $(foreach p,$(PAWN_SVGS),$(if $(wildcard $(subst -pawn-,-warband-,$(p))),\
                  $(subst -pawn-,-plate-,$(p:.svg=.stl))))

.PHONY: all tts print plates clean
all: tts print plates
tts: $(OBJS)
print: $(STLS)
plates: $(PLATES)

# svg_to_tts takes the bare basename ($*) and appends .obj/.png (and
# writes .mtl) itself; passing a name ending in .obj/.png is an error.
%.obj: %.svg $(SVG_TO_TTS)
	$(SVG_TO_TTS) $< $* $(TTS_FLAGS)

# Same basename convention; also writes $*-ink.svg alongside the .stl.
$(PAWN_STLS):    PRINT_FLAGS := $(PAWN_PRINT_FLAGS)
$(WARBAND_STLS): PRINT_FLAGS := $(WARBAND_PRINT_FLAGS)
%.stl: %.svg $(SVG_TO_PRINT)
	$(SVG_TO_PRINT) $< $* $(PRINT_FLAGS)

# COLOR-plate-VER.stl <- COLOR-pawn-VER.stl + COLOR-warband-VER.stl
.SECONDEXPANSION:
$(PLATES): $$(subst -plate-,-pawn-,$$@) $$(subst -plate-,-warband-,$$@) $(STL_PLATE)
	$(STL_PLATE) $@ $(word 1,$^):1 $(word 2,$^):$(WARBANDS_PER_PLATE) --gap $(PLATE_GAP)

clean:
	rm -f $(OBJS) $(PAWN_SVGS:.svg=.mtl) $(PAWN_SVGS:.svg=.png) $(STLS) $(INKS) $(PLATES)
