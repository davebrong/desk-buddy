// Lump monitor mount: a small adjustable shelf that sticks to the back of the
// monitor so the prism peeks over the top edge.
//
//   plate   sticks to the monitor (flat back, use your adhesive)
//   arm     the shelf; TILTS on a hinge (loosen the side knob, set, tighten)
//   cradle  holds the stock case; SWIVELS on the shelf (knob underneath)
//
// Everything is printed, including the thumbscrews. Export:
//   openscad -o mount_all.stl -D 'part="print_all"' lump_monitor_mount.scad
// Fit test first (~15 min): part="thread_test" prints one short screw + one knob nut.
// Print with supports OFF: every overhang here bridges or is <= 45 degrees.
//
// Coordinates: the monitor's back surface is the plane y = 0 (monitor at -y, toward you),
// +y points away from you, z is up along the monitor's back.

/* [What to render] */
part = "preview"; // [preview, print_all, reprint, plate_arm, thread_test, plate, arm, cradle, hinge_bolt, hinge_nut, swivel_bolt]
pitch = 12;       // preview only: tilt (+ leans Lump's top toward you)
yaw = 15;         // preview only: swivel

/* [Stock case] */
case_w = 33.4;
case_d = 50.1;
case_h = 37.2;
cube_size = 25.6;
cube_from_front = 12.2;  // case front edge -> cube front face (swivel axis sits under the cube)
clear = 0.6;

/* [Printed thumbscrews] */
th_d = 10;        // thread outer diameter
th_p = 2.5;       // pitch
th_e = 0.7;       // thread depth is 2x this
th_clear = 0.3;   // loosen (0.4) if the test nut is tight, tighten (0.2) if sloppy
knob_d = 22;
knob_h = 8;

/* [Plate and hinge] */
plate_w = 46;
plate_t = 4;
plate_below = 30;   // adhesive area below the hinge
hinge_w = 32;
knuckle_t = 7;
knuckle_r = 7;
hinge_gap = 0.3;

/* [Shelf and cradle] */
pivot_dist = 50;    // monitor back -> swivel axis; more = more tilt range
shelf_t = 5;
shelf_r = 16;
cradle_wall = 2;
cradle_floor = 8;
cradle_lip = 12;
usb_slot = 16;

$fn = 64;

// ---------- derived ----------
hole_d = th_d + 0.8;
yh = plate_t + knuckle_r + 0.6;          // hinge axis
zh = plate_below + knuckle_r;
z_shelf = zh + knuckle_r;                // shelf top
arm_w = hinge_w - 2 * knuckle_t - 2 * hinge_gap;
pf = cube_from_front + cube_size / 2;    // swivel axis -> case front
hinge_shank = hinge_w + knob_h + 1.5;
swivel_shank = shelf_t + cradle_floor - 1.8;

// ---------- printed thread (twisted eccentric circle: mates with itself) ----------
module thread_rod(len, grow = 0) {
    linear_extrude(height = len, twist = -360 * len / th_p, slices = ceil(len / th_p * 36), convexity = 4)
        translate([th_e, 0]) circle(r = th_d / 2 - th_e + grow, $fn = 48);
}

module knob() {
    difference() {
        cylinder(d = knob_d, h = knob_h);
        for (i = [0:7]) rotate(i * 45) translate([knob_d / 2 + 1, 0, -1]) cylinder(d = 6, h = knob_h + 2, $fn = 24);
        // soften the top edge so it's nice on the fingers
        translate([0, 0, knob_h - 1]) difference() {
            cylinder(d = knob_d + 2, h = 2);
            cylinder(d1 = knob_d, d2 = knob_d - 2, h = 1);
        }
    }
}

module bolt(shank) {
    knob();
    translate([0, 0, knob_h - 0.01]) intersection() {
        thread_rod(shank);
        union() {
            cylinder(d = th_d + 1, h = shank - 1.2);
            translate([0, 0, shank - 1.2]) cylinder(d1 = th_d, d2 = th_d - 2.4, h = 1.2);
        }
    }
}

module nut_knob() {
    difference() {
        knob();
        translate([0, 0, -1]) thread_rod(knob_h + 2, th_clear);
        translate([0, 0, -0.01]) cylinder(d1 = th_d + 1.2, d2 = th_d - 0.4, h = 0.8);
    }
}

module hinge_bolt() bolt(hinge_shank);
module swivel_bolt() bolt(swivel_shank);
module hinge_nut() nut_knob();

// ---------- helpers ----------
// 2D shape drawn in (y, z), extruded along +x by w
module yz_extrude(w) rotate([90, 0, 90]) linear_extrude(w) children();
module hinge_hole() translate([-50, yh, zh]) rotate([0, 90, 0]) cylinder(d = hole_d, h = 100);

// ---------- parts ----------
module plate() {
    difference() {
        union() {
            translate([-plate_w / 2, 0, 0]) cube([plate_w, plate_t, z_shelf - 2]);
            for (x0 = [-hinge_w / 2, hinge_w / 2 - knuckle_t])
                translate([x0, 0, 0]) yz_extrude(knuckle_t) hull() {
                    translate([0, zh - knuckle_r]) square([plate_t, 2 * knuckle_r - 2]);
                    translate([yh, zh]) circle(knuckle_r);
                }
        }
        hinge_hole();
        // room for the hinge knob and nut beside the knuckles (they hit a full-width plate in v1)
        for (sx = [-1, 1]) translate([sx > 0 ? hinge_w / 2 : -50 - hinge_w / 2, -1, zh - knob_d / 2 - 2]) cube([50, 50, 50]);
    }
}

module arm() {
    difference() {
        union() {
            translate([-arm_w / 2, 0, 0]) yz_extrude(arm_w) hull() {
                translate([yh, zh]) circle(knuckle_r);
                translate([yh, z_shelf - shelf_t]) square([pivot_dist - yh, shelf_t]);
            }
            translate([0, pivot_dist, z_shelf - shelf_t]) cylinder(r = shelf_r, h = shelf_t);
        }
        hinge_hole();
        translate([0, pivot_dist, -1]) cylinder(d = hole_d, h = 200);
        // flat seat for the swivel knob (v1's sloped underside caught the knob's edge)
        translate([0, pivot_dist, -1]) cylinder(d = knob_d + 2, h = z_shelf - shelf_t + 1);
    }
}

// local frame: swivel axis at origin, bottom at z = 0, front (toward you) = -y
module cradle() {
    iw = case_w + 2 * clear;
    id = case_d + 2 * clear;
    y0 = -pf - clear;
    difference() {
        translate([-iw / 2 - cradle_wall, y0 - cradle_wall, 0])
            cube([iw + 2 * cradle_wall, id + 2 * cradle_wall, cradle_floor + cradle_lip]);
        translate([-iw / 2, y0, cradle_floor]) cube([iw, id, 100]);
        translate([-usb_slot / 2, y0 + id - 1, cradle_floor]) cube([usb_slot, 20, 100]);   // USB at the back
        // threaded hole goes right through: no ceiling, so the slicer never fills it with support
        translate([0, 0, -0.01]) thread_rod(cradle_floor + 0.02, th_clear);
        translate([0, 0, -0.01]) cylinder(d1 = th_d + 1.2, d2 = th_d - 0.4, h = 0.8);
    }
}

// ---------- preview ----------
module ghost_case() {
    color("#1c1c1c") translate([-case_w / 2, -pf, cradle_floor]) cube([case_w, case_d, case_h]);
    color("#9fb4ff", 0.5) translate([-cube_size / 2, -cube_size / 2, cradle_floor + case_h - 5]) cube(cube_size);
}

module assembled() {
    // the monitor, ending roughly level with the case top
    color("#9a9a9a", 0.35) translate([-110, -22, -20]) cube([220, 22, z_shelf + cradle_floor + case_h + 20]);
    color("#e9e4d6") plate();
    color("#c9b98f") translate([-hinge_w / 2 - knob_h, yh, zh]) rotate([0, 90, 0]) hinge_bolt();
    color("#c9b98f") translate([hinge_w / 2, yh, zh]) rotate([0, 90, 0]) hinge_nut();
    translate([0, yh, zh]) rotate([pitch, 0, 0]) translate([0, -yh, -zh]) {
        color("#e9e4d6") arm();
        color("#c9b98f") translate([0, pivot_dist, z_shelf - shelf_t - knob_h]) swivel_bolt();
        translate([0, pivot_dist, z_shelf]) rotate([0, 0, yaw]) {
            color("#d8cdb0") cradle();
            ghost_case();
        }
    }
}

// ---------- print layouts (each part in its print orientation) ----------
module plate_print() translate([0, z_shelf - 2, 0]) rotate([90, 0, 0]) plate();       // adhesive face down
module arm_print() translate([0, pivot_dist + shelf_r, z_shelf]) rotate([180, 0, 0]) arm(); // shelf top down

// Packed in two rows (big parts, then knobs) and centred, to fit a 180 x 180 mm bed (A1 mini).
gap = 6;
cradle_hx = case_w / 2 + clear + cradle_wall;
cradle_y0 = -pf - clear - cradle_wall;
arm_hx = max(shelf_r, arm_w / 2);
arm_len = pivot_dist + shelf_r - (yh - knuckle_r);
row1_w = plate_w + 2 * cradle_hx + 2 * arm_hx + 2 * gap;
row1_h = max(z_shelf, case_d + 2 * clear + 2 * cradle_wall, arm_len);

module print_all(knobs = true) {
    translate([-row1_w / 2, knobs ? -(row1_h - knob_d - gap) / 2 : -row1_h / 2, 0]) {
        translate([plate_w / 2, 2, 0]) plate_print();
        translate([plate_w + gap + cradle_hx, -cradle_y0, 0]) cradle();
        translate([plate_w + 2 * cradle_hx + 2 * gap + arm_hx, 0, 0]) arm_print();
        if (knobs) for (i = [0:2]) translate([knob_d / 2 + i * (knob_d + gap), -gap - knob_d / 2, 0])
            if (i == 0) hinge_bolt(); else if (i == 1) swivel_bolt(); else hinge_nut();
    }
}

if (part == "print_all") print_all();
else if (part == "reprint") print_all(knobs = false);
else if (part == "plate_arm") translate([-(plate_w + gap + 2 * arm_hx) / 2, -row1_h / 2, 0]) {   // v2 plate + arm only
    translate([plate_w / 2, 2, 0]) plate_print();
    translate([plate_w + gap + arm_hx, 0, 0]) arm_print();
}   // v2 fixes: plate, cradle, arm (v1 knobs still fit)
else if (part == "thread_test") { swivel_bolt(); translate([28, 0, 0]) hinge_nut(); }
else if (part == "plate") plate_print();
else if (part == "arm") arm_print();
else if (part == "cradle") cradle();
else if (part == "hinge_bolt") hinge_bolt();
else if (part == "hinge_nut") hinge_nut();
else if (part == "swivel_bolt") swivel_bolt();
else assembled();

// How far can it tilt before the top of the case touches the monitor (swivel at 0)?
y_r = pivot_dist - pf - yh;
z_r = knuckle_r + cradle_floor + case_h;
max_pitch = max([for (p = [0:1:60]) if (yh + y_r * cos(p) - z_r * sin(p) >= 0) p]);
echo(str("Tilt range: up to ~", max_pitch, " deg toward you before the case top meets the monitor. ",
         "Shelf top sits ", z_shelf, " mm above the plate's bottom edge; case top ~",
         z_shelf + cradle_floor + case_h, " mm above it."));
