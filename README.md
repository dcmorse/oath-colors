# oath-colors

<p align="center">
  <img src="docs/bandit-king.jpg" height="280" alt="3D-printed green pieces with black decals, standing on the Oath board" title="3D printed, with decals">
  &nbsp;
  <img src="docs/tts.jpg" height="280" alt="Orange and teal custom pawns in Tabletop Simulator" title="In Tabletop Simulator">
</p>

## Bring a `.svg` of your new character idea and convert it for use in TTS and 3d printing

This repo contains command line tools for getting custom pawns into TTS and for 3d printing them.
Additionally, 3d printing custom pawn shapes is supported.

## System Requirements
- A unix-like system, and a little familiarity with it. I used Linux though MacOS will probably work without too much trouble. Windows might work if you knew your way around. 
- A willingness to run vibe-coded software by yours truly. I haven't even read some of the code. Beware, ha ha. 

## Workflow
1. Check out this repo to someplace you want to work.
2. Drop a player pawn and a player warband svg into that directory. I strongly recommend the file naming convention `<name>-{pawn,warband}-v<digits>.svg`. For example, `orange-pawn-v03.svg` and `orange-warband-v03.png`. 
3. Install [uv](https://docs.astral.sh/uv/), the python package manager.
4. _I probably forgot to list some other dependencies here, like `make`_
5. Type `make` and check the output for errors

## Outputs

### Tabletop Simulator

- `.obj` file
- `.png` file

#### Test It Out
In TTS go `Objects > Custom > MODEL`, click where you want the new pawn, then press esc, then put the path to the `.obj` in Model/Mesh, and path to the `.png` in Diffuse/Image. I checked `Figurine` too. Click OK. 

#### Publishing for the world

I don't personally know how to do this. With the Orange and Teal player factions, people have been collaborating with the TTS Oath mod maintainers to get them distributed. 

That said, I believe if you have a web server and host the two pawns on it, you can just give the TTS Model dialog URLs and all other players will see what you see. I'm not sure how you'd do the warbands. 

### Printing

- `.stl` files - there's one for the warband, one for the pawn, and one "plate" that is a combination of one pawn and 14 warbands - enough for a player faction. 
- `-ink.svg` file - I arrange these black markings onto a pdf and then print on decal paper. Then I cut out the decals and apply them with hot water. See `README.md`s in super-directories for tips and tricks on that. 

Custom tuckboxes and player boards are outside the scope of this project. 

### Blender &c

- `.mtl` file - material file that links the `.obj` and `.png` files in some 3d editors. I haven't tested it and recommend you ignore it. 

