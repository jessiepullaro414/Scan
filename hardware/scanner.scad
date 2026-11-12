// =====================================================================
//  OpenHandScan - open-source handheld 3D scanner (markerless + markers)
//  Parametric housing (OpenSCAD).  Units: mm.   License: MIT (see LICENSE)
//
//  Sensors in the head:
//    * hardware-synced stereo pair (ELP dual-lens OV9281, one USB cable)
//    * random-dot projector  (650 nm dot laser + 10k-dot DOE)  -> dense
//      stereo depth on plain surfaces = markerless tracking
//    * vertical-line laser  (right)  \  "+" pattern -> high-accuracy
//    * horizontal-line laser (top)   /   laser-plane triangulation
//    * IMU on the back cover (motion prior for tracking)
//
//  World frame: X = right, Y = forward (toward the object), Z = up.
//  Head front face is y = 0; the faceplate sits proud of it (y 0..fp_t).
//
//          [H laser]
//              |   \
//          [dot proj]\  brace
//       [ L-cam   R-cam ]--[V laser]
//              |
//            (grip)
//
//  The faceplate is a separate part so that ONLY it needs reprinting if
//  your stereo board's dimensions differ from the placeholders below.
//
//  Export:  openscad -D part=\"head\" -o head.stl scanner.scad
// =====================================================================

part = "assembly"; // [assembly, head, faceplate, back_cover, grip_left, grip_right, led_cartridge]
show_fans = true;
projector = "led"; // [led, laser]  led = LED + printed mask + M12 lens (the current build);  laser = dot laser + DOE disc
$fn = 72;
eps = 0.01;

/* [Optics] */
work_dist = 250;  // laser sheets cross here, in front of the faceplate
base_v    = 92;   // camera centre -> vertical-line laser block centre (X)
base_h    = 80;   // camera centre -> horizontal-line laser block centre (Z)
proj_z    = 42;   // dot projector height above camera centre

/* [Stereo camera board -- ELP OV9281 dual board, measured 2026-11-12] */
st_board      = [80, 16.5];  // PCB width (X), height (Z)
st_board_t    = 1.6;
st_base       = 60;        // lens centre-to-centre distance
// 4 M2 holes, one per corner, hole edge 0.8 mm in from the board edges -> centres 0.8 + 1.1 = 1.9 mm in
st_hole_in    = 1.9;
st_holes      = [for (sx=[-1,1], sz=[-1,1]) [sx*(st_board[0]/2 - st_hole_in), sz*(st_board[1]/2 - st_hole_in)]];
st_pilot_d    = 1.8;       // M2 self-tapping
st_standoff_d = 4.4;       // clears the lens openings (posts sit ~10.3 mm from the lens axes)
st_lens_d     = 14;        // lens + holder, round
st_lens_len   = 16;        // lens front above the PCB front
st_standoff_h = 10;        // faceplate back -> PCB front; lenses stand 16 - 10 - fp_t = 2 mm proud, to grip for focus
st_lens_hole  = 16;        // M12 lenses pass through (focus from the front)

/* [Head] */
head_d      = 36;
blk         = [120, 56];   // camera block outer X, Z
pocket      = [106, 40];   // through-opening X, Z (max stereo board size ~104 x 38)
fp_t        = 4;           // faceplate thickness
arm_w       = 16;
harm_w      = 22;          // vertical arm is wider: it carries the dot projector
lblk_w      = 28;
lblk_l      = 28;
brace_d     = 10;
brace_depth = 20;
groove_w    = 5;
groove_d    = 4;
cable_slot  = [16, 10];

/* [12 x 35 mm laser modules] */
laser_d        = 12;
laser_len      = 35;
laser_clear    = 0.35;
setscrew_pilot = 2.6;      // M3 screw self-taps, pinches the module
doe_d          = 5;        // DOE disc (Lasermate 5 mm PET)
doe_recess     = 0.5;
doe_lip        = 1.2;      // printed lip in front of the DOE
doe_aperture   = 4.5;

/* [LED pattern projector (projector = "led")] */
led_proj_z   = 46;    // lens axis height above the camera centre, on the front face
// M12 lens holder with 20 mm hole spacing:
//   arducam_metal      = Arducam metal holder (LHK01 kit, 10 mm tall U0756M10): round 14 mm base,
//                        two ears in line, M2 screws
//   commonlands_cla020 = Commonlands CLA020: 20 x 20 mm square, holes on the diagonal, M1.6 screws
holder_type  = "arducam_metal"; // [arducam_metal, commonlands_cla020]
holder_h     = 10;    // holder height; must match the holder you use
holder_hole_sp = 20;
cla          = holder_type == "commonlands_cla020";
holder_fp    = cla ? [20, 20] : [26, 15];     // footprint X x Z incl. ears
holder_holes = cla ? "diagonal" : "inline";
holder_screw = cla ? 1.9 : 2.4;               // M1.6 / M2 clearance
led_front_t  = 4.6;   // front wall (thicker because the bay is tilted)
led_lens_hole = 17;
led_cav      = [holder_fp[0] + 1.5, holder_fp[1] + 2.5];  // front cavity around the lens holder
led_bay      = 30;    // cartridge + 25 x 25 mm heatsink
led_block_w  = 40;
cart_plate_t = 2.0;   // clamps mask + diffuser under the holder
cart_gap     = 2.5;   // clearance for the LED dome
star_d       = 20.6;  // 20 mm LED star
star_t       = 1.7;
mask_win     = [6.4, 5.0];
heatsink     = [25, 25, 20];

/* [Fasteners] */
ins_d     = 4.0;   // M3 x 5.7 heat-set insert (OD 4.6)
ins_depth = 6.5;
m3_clear  = 3.4;
m3_head_d = 6.5;
m3_head_h = 3.5;
cover_t   = 3;
flush_screws = true;  // faceplate and back cover: countersunk (flat-head) M3 screws, flush with the surface
csk_d     = 6.4;      // countersink diameter at the surface (90 degree cone)

/* [Back cover parts] */
toggle_d   = 6.6;        // MTS-102 mini toggle = laser kill switch (fits M6 or 1/4-40 thread)
toggle_pos = [40, -12];  // world [x, z]
imu_pos    = [-30, 0];   // world [x, z]; Adafruit 1.0 x 0.7 in breakout
imu_holes  = [20.32, 12.7];
imu_standoff_h = 5;

/* [Grip] */
grip_angle  = 15;
grip_len    = 120;
grip_x      = 30;
grip_y      = 34;
grip_r      = 5;    // leaves a 20 mm flat front face for the 16 mm button bezel
grip_wall   = 2.5;
ledge_t     = 14;   // solid top of the grip: takes the screws from above and from the yoke
grip_screws = [[7.5, -10], [-7.5, -10], [7.5, -26], [-7.5, -26]];
button_d    = 16.2; // Adafruit 16 mm panel-mount momentary pushbutton (#1502-#1505)
button_z    = -30;  // the button's body tilts up inside: keep it below the ledge
pico_cz     = -64;
pico_standoff_h = 4;
boss_d      = 9;
grip_r_back = 8;    // rear (palm) corners
grip_ring_n = 8;    // shallow rings around the lower grip
grip_ring_pitch = 8;
grip_ring_w = 2;
grip_ring_d = 0.7;

/* [Yoke: the head's two cheeks that clamp the top of the grip] */
yoke_h      = 16;   // how far the cheeks reach down the grip
yoke_t      = 7;    // cheek thickness at its lower end
yoke_reach  = 44;   // where the gusset meets the head's underside (X from the centre)
yoke_fit    = 0.2;  // gap to the grip per side
yoke_screw_z = 10.5;        // cross screws: below the head's underside ...
yoke_screw_y = [-9, -31];   // ... at these world Y (clear of the cable slot and the top inserts)
yoke_cb_floor = 4.3;        // plastic left under the screw head (M3 x 10 reaches the insert)

/* [Alignment pins and edges] */
pin_d     = 3;      // printed pins: faceplate and back cover into the head
pin_h     = 3;
pin_fit   = 0.3;    // hole = pin + this
pin_pos   = [[-56, 9], [56, -9]];  // world x, z. The second hole is a short slot
gpin_d    = 4;      // pins between the grip halves
gpin_h    = 3;
gpin_pos  = [[-7, -7], [-31, -7]]; // world y, z below the head's underside
corner_r  = 3;      // outline radius of the head, faceplate and cover
edge_c    = 1.2;    // chamfer on the visible outer edges
logo      = "OpenHandScan";

// ------------------------------- derived -------------------------------
bw = blk[0];  bh = blk[1];
pw = pocket[0]; ph = pocket[1];
head_bot = -bh/2;
grip_cy  = -head_d/2;
a_v = atan(base_v / (work_dist + fp_t + head_d/2));
a_h = atan(base_h / (work_dist + fp_t + head_d/2));
led_tilt   = atan(led_proj_z / (work_dist + fp_t));   // aim the pattern centre at the stereo axis
cart_t     = cart_plate_t + cart_gap + star_t;
led_ledge  = led_front_t + holder_h + 1;               // depth of the cartridge seat (local frame)
led_shift  = (head_d + 6) * sin(led_tilt);             // how far the tilted bay rises at the back
led_z0     = led_proj_z - led_bay/2 - 4;               // projector block extent (axis aligned)
led_z1     = led_proj_z + led_shift + led_bay/2 + 3;
holder_hole_pos = holder_holes == "diagonal"
    ? [[ holder_hole_sp/(2*sqrt(2)),  holder_hole_sp/(2*sqrt(2))], [-holder_hole_sp/(2*sqrt(2)), -holder_hole_sp/(2*sqrt(2))]]
    : [[ holder_hole_sp/2, 0], [-holder_hole_sp/2, 0]];
cart_screw_pos = [for (sx=[-1,1], sy=[-1,1]) [sx*12.6, sy*12.6]];
// faceplate / back cover screw positions (world x, z)
frame_screws = [for (sx=[-1,1], sz=[-1,1]) [sx*(bw/2 - 3.5), sz*(bh/2 - 4)],
                [0, bh/2 - 4], [0, -(bh/2 - 4)]];

// ------------------------------- helpers -------------------------------
module ycyl(d, h, center=false) rotate([-90,0,0]) cylinder(d=d, h=h, center=center);
module xcyl(d, h, center=false) rotate([0,90,0])  cylinder(d=d, h=h, center=center);
module rrect(x, y, r, h)
    hull() for (sx=[-1,1], sy=[-1,1])
        translate([sx*(x/2-r), sy*(y/2-r), 0]) cylinder(r=r, h=h);

module laser_v_frame() translate([base_v, -head_d/2, 0])  rotate([0,0,a_v])  children();
module laser_h_frame() translate([0, -head_d/2, base_h])  rotate([-a_h,0,0]) children();
module proj_frame()    translate([0, -head_d/2, proj_z])  children();
// LED projector: local +Y = optical axis (tilted down), origin on the front face at the lens axis
module led_frame()     translate([0, 0, led_proj_z]) rotate([-led_tilt, 0, 0]) children();
module grip_frame()    translate([0, grip_cy, head_bot])  rotate([-grip_angle,0,0]) children();

// ---- shared small features ----
// a pin with a lead-in cone, standing on z = 0
module pin(d, h) { cylinder(d=d, h=h - d/4 + eps); translate([0, 0, h - d/4]) cylinder(d1=d, d2=d/2, h=d/4); }
// its hole (along +z from z = 0); slot > 0 makes it a short slot along x
module pin_hole(d, h, slot=0)
    hull() for (sx=[-1,1]) translate([sx*slot/2, 0, -eps]) cylinder(d=d + pin_fit, h=h + 0.6 + eps);
// an M3 hole through a plate of thickness t, from its z = 0 (outside) face: countersunk for a
// flat-head screw, or (flush_screws = false) as before: a shallow counterbore, or plain
module screw_seat(t, plain=false) {
    cylinder(d=m3_clear, h=t + 2*eps);
    if (flush_screws) cylinder(d1=csk_d + 2*eps, d2=m3_clear, h=(csk_d - m3_clear)/2 + eps);
    else if (!plain) cylinder(d=m3_head_d, h=1.8);
}
// a flat plate with rounded corners and a chamfer around its z = 0 face
module plate(x, y, t) {
    n = 4;
    for (i=[0:n-1]) translate([0, 0, i*edge_c/n])
        linear_extrude(edge_c/n + eps) offset(delta=-(n - i)*edge_c/n) offset(r=corner_r) square([x - 2*corner_r, y - 2*corner_r], center=true);
    translate([0, 0, edge_c]) linear_extrude(t - edge_c) offset(r=corner_r) square([x - 2*corner_r, y - 2*corner_r], center=true);
}

// =============================== HEAD ==================================
// The head's outline, seen from the front (2D x = world x, 2D y = world z).
module head_outline_raw() {
    translate([-bw/2, -bh/2]) square([bw, bh]);                                   // camera block
    translate([bw/2 - 1, -arm_w/2]) square([base_v - bw/2, arm_w]);               // V arm
    translate([base_v - lblk_l/2, -lblk_w/2]) square([lblk_l, lblk_w]);           // V laser block
    translate([-harm_w/2, bh/2 - 1]) square([harm_w, base_h - bh/2]);             // H arm (+projector)
    translate([-lblk_w/2, base_h - lblk_l/2]) square([lblk_w, lblk_l]);           // H laser block
    if (projector == "led")                                                        // LED projector block
        translate([-led_block_w/2, led_z0]) square([led_block_w, led_z1 - led_z0]);
    xi = grip_x/2 + yoke_fit;                                                      // yoke: cheeks + gussets
    for (sx=[-1,1]) scale([sx, 1])
        polygon([[xi, head_bot + 1], [xi, head_bot - yoke_h], [xi + yoke_t, head_bot - yoke_h],
                 [yoke_reach, head_bot], [yoke_reach, head_bot + 1]]);
}
// inside corners filled (stiffer arms), outside corners rounded; the grip's slot stays square
module head_outline() difference() {
    offset(r=corner_r) offset(r=-corner_r - 6) offset(r=6) head_outline_raw();
    translate([-grip_x/2 - yoke_fit, head_bot - yoke_h - 10]) square([grip_x + 2*yoke_fit, yoke_h + 10]);
}
module head_body() {
    n = 4;                                                                         // chamfered back edge
    rotate([90, 0, 0]) {
        linear_extrude(head_d - edge_c) head_outline();
        for (i=[0:n-1]) translate([0, 0, head_d - edge_c + i*edge_c/n - eps])
            linear_extrude(edge_c/n + eps) offset(delta=-(i + 1)*edge_c/n) head_outline();
    }
    hull() {                                                                              // brace
        // ends sit 1 mm inside the laser blocks (a tangent contact makes a non-manifold vertex)
        translate([base_v - lblk_l/2 - brace_d/2 + 1, -brace_depth, lblk_w/2 - brace_d/2 + 1]) ycyl(brace_d, brace_depth);
        translate([lblk_w/2 - brace_d/2 + 1, -brace_depth, base_h - lblk_l/2 - brace_d/2 + 1]) ycyl(brace_d, brace_depth);
    }
}

module head() {
    difference() {
        head_body();
        // through-opening for the stereo board (faceplate in front, cover behind)
        translate([-pw/2, -head_d - 1, -ph/2]) cube([pw, head_d + 2, ph]);
        // line-laser bores + pinch screws
        laser_v_frame() ycyl(laser_d + laser_clear, 120, center=true);
        laser_h_frame() ycyl(laser_d + laser_clear, 120, center=true);
        translate([base_v, -head_d/2, 0]) cylinder(d=setscrew_pilot, h=lblk_w/2 + 1);
        translate([0, -head_d/2, base_h]) xcyl(setscrew_pilot, lblk_w/2 + 1);
        // LED projector: lens opening, holder cavity, cartridge + heatsink bay (tilted)
        if (projector == "led") led_cuts();
        // laser dot projector: bore from the back, DOE pocket + lip + flared aperture at the front
        if (projector == "laser") proj_frame() {
            translate([0, -head_d/2 - 1, 0]) ycyl(laser_d + laser_clear, head_d + 1 - doe_lip - doe_recess);
            translate([0, head_d/2 - doe_lip - doe_recess - eps, 0]) ycyl(doe_d + 0.4, doe_recess + 2*eps);
            translate([0, head_d/2 - doe_lip - eps, 0]) rotate([-90,0,0])
                cylinder(d1=doe_aperture, d2=doe_aperture + 2*doe_lip + 2*eps, h=doe_lip + 2*eps);
            xcyl(setscrew_pilot, harm_w/2 + 1);
        }
        // wire channels on the back face
        translate([pw/2 - 1, -head_d - eps, -groove_w/2]) cube([base_v - pw/2 + 7, groove_d + eps, groove_w]);
        translate([-groove_w/2, -head_d - eps, ph/2 - 1]) cube([groove_w, groove_d + eps, base_h - ph/2 + 7]);
        // cable slot down into the grip
        translate([-cable_slot[0]/2, grip_cy - cable_slot[1]/2, head_bot - eps])
            cube([cable_slot[0], cable_slot[1], bh/2 - ph/2 + 2*eps]);
        // grip screws: M3 x 12 dropped in from the opening, counterbored
        for (s = grip_screws) {
            translate([s[0], s[1], head_bot - eps]) cylinder(d=m3_clear, h=bh/2 - ph/2 + 2*eps);
            translate([s[0], s[1], -ph/2 - 3]) cylinder(d=m3_head_d, h=3 + eps);
        }
        // heat-set inserts: faceplate (front) and back cover (back)
        for (p = frame_screws) {
            translate([p[0], -ins_depth, p[1]]) ycyl(ins_d, ins_depth + eps);
            translate([p[0], -head_d - eps, p[1]]) ycyl(ins_d, ins_depth + eps);
        }
        // alignment pin holes: faceplate pins (front), back cover pins (back, mirrored in z)
        for (i = [0:1]) {
            translate([pin_pos[i][0], 0, pin_pos[i][1]]) rotate([90, 0, 0]) pin_hole(pin_d, pin_h, i*0.8);
            translate([pin_pos[i][0], -head_d, pin_pos[i][1]]) rotate([-90, 0, 0]) pin_hole(pin_d, pin_h, i*0.8);
        }
        // yoke cross screws: M3 x 10 from each side into the grip's ledge
        for (sx=[-1,1], y = yoke_screw_y) translate([0, y, head_bot - yoke_screw_z]) scale([sx, 1, 1]) {
            xcyl(m3_clear, yoke_reach + 1);
            translate([grip_x/2 + yoke_fit + yoke_cb_floor, 0, 0]) xcyl(m3_head_d, yoke_reach);
        }
    }
}

// ======================= LED PATTERN PROJECTOR =========================
// Stack along the (tilted) optical axis, front to back:
//   M12 lens | M12 lens holder (in the front cavity) | mask film + diffuser film |
//   cartridge (clamps the films, carries the LED star) | 25 x 25 mm heatsink (vents out the back)
module led_cuts() led_frame() {
    translate([0, -led_front_t - 1, 0]) ycyl(led_lens_hole, led_front_t + 12);                  // lens opening
    translate([-led_cav[0]/2, -led_ledge, -led_cav[1]/2]) cube([led_cav[0], led_ledge - led_front_t, led_cav[1]]); // holder cavity
    translate([-led_bay/2, -led_ledge - 60, -led_bay/2]) cube([led_bay, 60 + eps, led_bay]);    // cartridge bay
    for (p = cart_screw_pos)                                                                     // M2 pilots in the ledge
        translate([p[0], -led_ledge - eps, p[1]]) ycyl(1.7, 5);
}

// Cartridge, modelled flat: z = 0 is its front face (against the lens holder), print it that way.
module led_cartridge() {
    s = led_bay - 0.6;
    difference() {
        translate([-s/2, -s/2, 0]) cube([s, s, cart_t]);
        translate([-5.2, -5.2, -eps]) cube([10.4, 10.4, 0.3 + eps]);                             // film locating recess
        translate([-mask_win[0]/2, -mask_win[1]/2, -eps])
            cube([mask_win[0], mask_win[1], cart_plate_t + 2*eps]);                              // light window
        translate([0, 0, cart_plate_t]) cylinder(d=14, h=cart_gap + eps);                        // LED dome clearance
        translate([0, 0, cart_plate_t + cart_gap]) cylinder(d=star_d, h=star_t + eps);           // LED star pocket
        for (p = holder_hole_pos) translate([p[0], p[1], -eps]) {                                // M2 (or M1.6) x 6 into the holder
            cylinder(d=holder_screw, h=cart_t + 2*eps);
            translate([0, 0, cart_plate_t + 0.6]) cylinder(d=3.4, h=cart_t);
        }
        for (p = cart_screw_pos) translate([p[0], p[1], -eps]) {                                 // M2 x 8 into the ledge
            cylinder(d=2.4, h=cart_t + 2*eps);
            translate([0, 0, cart_plate_t + 1.0]) cylinder(d=4.2, h=cart_t);
        }
        translate([-2, s/2 - 4, cart_plate_t + cart_gap]) cube([4, 5, star_t + eps]);            // LED lead notch
    }
}

// ============================= FACEPLATE ===============================
// Modelled flat: z = 0 is the front (bed side), local XY = world XZ.
module faceplate() {
    difference() {
        union() {
            plate(bw, bh, fp_t);
            for (h = st_holes) translate([h[0], h[1], fp_t - eps])
                cylinder(d=st_standoff_d, h=st_standoff_h + eps);
            for (p = pin_pos) translate([p[0], p[1], fp_t - eps]) pin(pin_d, pin_h);   // into the head's front
        }
        for (sx=[-1,1]) translate([sx*st_base/2, 0, -eps])               // lens openings, 45 deg flare
            cylinder(d1=st_lens_hole + 2*fp_t, d2=st_lens_hole, h=fp_t + 2*eps);
        for (h = st_holes) translate([h[0], h[1], fp_t + 1])
            cylinder(d=st_pilot_d, h=st_standoff_h);
        for (p = frame_screws) translate([p[0], p[1], -eps]) screw_seat(fp_t);
    }
}

// ============================ BACK COVER ===============================
// Modelled flat: z = 0 is the outside (bed side). Local x = world x,
// local y = -world z (the part is flipped when installed).
module back_cover() {
    difference() {
        union() {
            plate(bw, bh, cover_t);
            for (sx=[-1,1], sy=[-1,1])
                translate([imu_pos[0] + sx*imu_holes[0]/2, -imu_pos[1] + sy*imu_holes[1]/2, cover_t - eps])
                    cylinder(d=5, h=imu_standoff_h + eps);
            for (p = pin_pos) translate([p[0], -p[1], cover_t - eps]) pin(pin_d, pin_h);   // into the head's back
        }
        // name, sunk into the outside (flipped here, like the part itself, so it reads from behind the scanner)
        translate([0, -14, -eps]) mirror([0, 1, 0]) linear_extrude(0.6)
            text(logo, size=5.5, font="Liberation Sans:style=Bold", halign="center", valign="center", spacing=1.05);
        for (p = frame_screws) translate([p[0], -p[1], -eps]) screw_seat(cover_t, plain=true);
        translate([toggle_pos[0], -toggle_pos[1], -eps]) cylinder(d=toggle_d, h=cover_t + 2*eps);
        for (sx=[-1,1], sy=[-1,1])
            translate([imu_pos[0] + sx*imu_holes[0]/2, -imu_pos[1] + sy*imu_holes[1]/2, cover_t + 1])
                cylinder(d=2.1, h=imu_standoff_h);     // M2.5 self-tapping
    }
}

// =============================== GRIP ==================================
// The grip is lofted through sections along its length (local frame, z along the grip). Its width
// (X) stays constant, so each half still prints flat on its outer face; the shape is in the side
// view: straight at the top (yoke, button), a relief for the fingers under the button, a swell
// for the palm, and a hook at the bottom that keeps the hand from sliding off.
grip_st = [  // [z, front y, rear y]
    [  25,   grip_y/2, -grip_y/2], [ -16,   grip_y/2, -grip_y/2], [ -40,   grip_y/2, -18.5],
    [ -52,   16,       -20],       [ -68,   16.3,     -21],       [ -88,   16.5,     -20],
    [-104,   17.5,     -18],       [-113,   20,       -17.5],     [-117.5, 19.5,     -17],
    [-grip_len, 17,    -14.5]];
// one thin section: flat front for the button, rounder at the palm
module grip_slice(s, inset) translate([0, 0, s[0]]) linear_extrude(0.01) hull() for (sx=[-1,1]) {
    translate([sx*(grip_x/2 - grip_r), s[1] - grip_r]) circle(r=grip_r - inset);
    translate([sx*(grip_x/2 - grip_r_back), s[2] + grip_r_back]) circle(r=grip_r_back - inset);
}
module grip_loft(inset=0) for (i=[0:len(grip_st)-2])
    hull() { grip_slice(grip_st[i], inset); grip_slice(grip_st[i+1], inset); }
module grip_outer_l() difference() {
    grip_loft();
    difference() {                                                                  // grip rings
        for (i=[0:grip_ring_n-1]) translate([-50, -50, -grip_len + 14 + i*grip_ring_pitch]) cube([100, 100, grip_ring_w]);
        grip_loft(grip_ring_d);
    }
}
module grip_inner_l() intersection() {
    grip_loft(grip_wall);
    translate([-50, -50, -grip_len + grip_wall]) cube([100, 100, grip_len + 40]);
}
module below_head() translate([-100, -150, head_bot - 300]) cube([200, 300, 300]);
module boss_pos() for (sy=[-1,1])
    translate([0, sy*(grip_y/2 - grip_wall - 3), -grip_len + grip_wall + boss_d/2 - 0.5]) children(); // sunk 0.5 mm into the floor (avoids a tangent, non-manifold contact)
module pico_pos() for (sy=[-1,1], sz=[-1,1])
    translate([0, sy*5.7, pico_cz + sz*23.5]) children();

module grip_full() {
    difference() {
        union() {
            difference() {
                intersection() { grip_frame() grip_outer_l(); below_head(); }
                intersection() {                               // hollow stops below the top ledge
                    grip_frame() grip_inner_l();
                    translate([-100, -150, head_bot - ledge_t - 300]) cube([200, 300, 300]);
                }
            }
            grip_frame() {
                intersection() {                               // bosses stay inside the rounded shell
                    boss_pos() xcyl(boss_d, grip_x - grip_wall, center=true);
                    grip_outer_l();
                }
                pico_pos() translate([grip_x/2 - grip_wall - pico_standoff_h, 0, 0])
                    xcyl(5, pico_standoff_h + 0.5);
            }
        }
        translate([-cable_slot[0]/2, grip_cy - cable_slot[1]/2, head_bot - ledge_t - 1])
            cube([cable_slot[0], cable_slot[1], ledge_t + 2]);
        for (s = grip_screws)
            translate([s[0], s[1], head_bot - ins_depth]) cylinder(d=ins_d, h=ins_depth + eps);
        for (sx=[-1,1], y = yoke_screw_y)                      // inserts for the yoke's cross screws
            translate([sx*(grip_x/2 - ins_depth + 0.3), y, head_bot - yoke_screw_z]) scale([sx, 1, 1]) xcyl(ins_d, ins_depth);
        grip_frame() {
            translate([-cable_slot[0]/2, -cable_slot[1]/2, -grip_len - 1])
                cube([cable_slot[0], cable_slot[1], grip_wall + 2]);
            translate([0, grip_y/2 - grip_wall - 1, button_z]) ycyl(button_d, grip_wall + 2);
            pico_pos() translate([grip_x/2 - grip_wall - pico_standoff_h - 1, 0, 0])
                xcyl(1.8, pico_standoff_h);
        }
    }
}

module grip_left() {
    difference() {
        intersection() { grip_full(); translate([-100, -150, -400]) cube([100, 300, 500]); }
        grip_frame() boss_pos() translate([-ins_depth, 0, 0]) xcyl(ins_d, ins_depth + eps);
        for (p = gpin_pos) translate([0, p[0], head_bot + p[1]])                 // holes for the right half's pins
            rotate([0, -90, 0]) hull() for (s=[-1,1]) translate([0, 0, -eps]) cylinder(d=gpin_d + pin_fit, h=gpin_h + 0.6);
    }
}

module grip_right() {
    difference() {
        intersection() { grip_full(); translate([0, -150, -400]) cube([100, 300, 500]); }
        grip_frame() boss_pos() {
            translate([-eps, 0, 0]) xcyl(m3_clear, grip_x);
            translate([grip_x/2 - m3_head_h, 0, 0]) xcyl(m3_head_d, m3_head_h + 1);
        }
    }
    for (p = gpin_pos) translate([eps, p[0], head_bot + p[1]]) rotate([0, -90, 0]) pin(gpin_d, gpin_h);  // into the left half
}

// ============================= DUMMIES =================================
module dummy_stereo() {
    yb = -st_standoff_h;
    color("darkgreen") translate([-st_board[0]/2, yb - st_board_t, -st_board[1]/2])
        cube([st_board[0], st_board_t, st_board[1]]);
    for (sx=[-1,1]) translate([sx*st_base/2, 0, 0])
        color("black") translate([0, yb, 0]) ycyl(st_lens_d, st_lens_len);
}
module dummy_laser(c="silver") {
    color(c) translate([0, -laser_len/2, 0]) ycyl(laser_d, laser_len);
}
module fan(vertical) {
    L = (work_dist + head_d/2) / cos(vertical ? a_v : a_h) + 40;
    w = 2 * L * tan(30);
    color([1, 0, 0, 0.18]) hull() {
        translate([0, laser_len/2, 0]) cube(0.4, center=true);
        translate([0, laser_len/2 + L, 0]) cube(vertical ? [0.4, 0.4, w] : [w, 0.4, 0.4], center=true);
    }
}
module dot_cone() {
    L = work_dist + head_d/2;
    color([1, 0.3, 0.3, 0.10]) hull() {
        translate([0, head_d/2, 0]) cube(0.4, center=true);
        translate([0, L, 0]) cube([2*L*tan(40), 0.4, 2*L*tan(32)], center=true);
    }
}
module dummy_pico() {
    color("green") grip_frame()
        translate([grip_x/2 - grip_wall - pico_standoff_h - 1, -10.5, pico_cz - 25.5]) cube([1, 21, 51]);
}

module assembly() {
    color("#3b4048") head();
    color("#17181b") translate([0, fp_t, 0]) rotate([90,0,0]) faceplate();
    color("#17181b") translate([0, -head_d - cover_t, 0]) rotate([-90,0,0]) back_cover();
    color("#e8762c") grip_left();
    color("#e8762c") grip_right();
    dummy_stereo();
    laser_v_frame() { dummy_laser(); if (show_fans) fan(true); }
    laser_h_frame() { dummy_laser(); if (show_fans) fan(false); }
    if (projector == "laser")
        proj_frame() { translate([0, -0.5 - doe_lip - doe_recess, 0]) dummy_laser("gold"); if (show_fans) dot_cone(); }
    if (projector == "led") led_frame() {
        color("#4a6fa5") translate([0, -led_ledge, 0]) rotate([90, 0, 0]) led_cartridge();
        color("dimgray") translate([-holder_fp[0]/2, -led_ledge, -holder_fp[1]/2]) cube([holder_fp[0], holder_h, holder_fp[1]]);
        color("black") translate([0, -led_ledge + holder_h - 5, 0]) ycyl(14, 18);
        color("white") translate([0, -led_ledge - cart_plate_t - cart_gap - star_t, 0]) ycyl(20, star_t);
        color("gray") translate([-heatsink[0]/2, -led_ledge - cart_t - heatsink[2], -heatsink[1]/2])
            cube([heatsink[0], heatsink[2], heatsink[1]]);
        if (show_fans) {
            L = work_dist + fp_t;
            color([1, 0.4, 0.3, 0.12]) hull() {
                translate([0, 12, 0]) cube(0.4, center=true);
                translate([0, L, 0]) cube([2*L*tan(37.9), 0.4, 2*L*tan(30.3)], center=true); // 3.6 mm lens, 5.6 x 4.2 mm mask
            }
        }
    }
    dummy_pico();
    color("black") grip_frame() translate([0, grip_y/2, button_z]) ycyl(11, 4);
    color("silver") translate([toggle_pos[0], -head_d - cover_t, toggle_pos[1]]) rotate([90,0,0]) cylinder(d=3, h=10);
    color("purple") translate([imu_pos[0] - 12.7, -head_d + imu_standoff_h, imu_pos[1] - 8.9]) cube([25.4, 1.6, 17.8]);
    if (show_fans) color("yellow") translate([0, work_dist + fp_t, 0]) sphere(d=6);
}

// ============================ DISPATCH =================================
if (part == "assembly")   assembly();
if (part == "head")       rotate([-90,0,0]) head();                                   // front face on bed
if (part == "faceplate")  faceplate();
if (part == "back_cover") back_cover();
if (part == "led_cartridge") led_cartridge();                                         // front face on bed
if (part == "grip_left")  translate([0,0,grip_x/2]) rotate([0,-90,0]) grip_left();   // outer face on bed
if (part == "grip_right") translate([0,0,grip_x/2]) rotate([0, 90,0]) grip_right();  // outer face on bed
