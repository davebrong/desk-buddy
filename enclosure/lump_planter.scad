// Lump planter: a small pot that hides the stock black case.
// The case drops into the pot, a "moss deck" lid sits over it with a
// hole for the prism cube, and you glue preserved moss onto the deck.
//
// Export each part separately:
//   openscad -o pot.stl  -D 'part="pot"'  lump_planter.scad
//   openscad -o deck.stl -D 'part="deck"' lump_planter.scad
// Print the pot upright and the deck flat. No supports needed.
//
// Coordinates: front of Lump (the face you look into) = -Y, USB/back = +Y.

/* [What to render] */
part = "preview"; // [preview, section, pot, deck]

/* [Stock case - measure with calipers] */
case_w = 33.4;          // left-right
case_d = 50.1;          // front-back, longest point
case_h = 37.2;          // tallest point of the black case
cube_size = 25.6;       // prism cube edge (TODO: measure; estimated from 58 total - 37.2 + 5 recess)
cube_from_front = 12.2; // case front edge -> cube front face (TODO: measure; default assumes centred)
cube_x_offset = 0;      // + moves cube hole right, if the cube isn't centred side-to-side
usb_z = 12;             // USB-C port centre height above the case bottom (TODO: measure)

/* [Fit] */
clear = 0.6;            // gap around the case
deck_clear = 0.8;       // gap around the cube in the deck hole
deck_gap = 0.4;         // deck floats this far above the case top

/* [Pot shape] */
pot_shape = "round";    // [round, box]
box_corner_r = 4;       // corner rounding for the box shape
wall = 2.4;
floor_t = 2.4;
lift = 0;               // raise Lump: extra solid height under the case (e.g. 20)
taper = 7;              // degrees of outward flare per side
rim_h = 9;              // height of the terracotta-style rim band
rim_lip = 2.6;          // how far the rim sticks out
rim_above_deck = 4;     // pocket depth for the moss above the deck

/* [Inside bits] */
ledge_w = 2;            // ledge the deck rests on
deck_t = 1.6;
deck_holes = true;      // little holes in the deck so glue/moss grips
fence_h = 5;            // locating fence around the case footprint
fence_t = 1.6;
notch_w = 14;           // cable notch at the back (fits a USB-C plug body)
push_hole_d = 18;       // hole in the floor to push the case up and out

$fn = 128;

// ---------- derived ----------
F = floor_t + lift;                    // case bottom
z_case_top = F + case_h;
z_ledge = z_case_top + deck_gap;       // deck bottom
z_deck_top = z_ledge + deck_t;
H = z_deck_top + rim_above_deck;       // pot top
fence_hx = case_w / 2 + clear + fence_t;
fence_hy = case_d / 2 + clear + fence_t;
cube_yc = -case_d / 2 + cube_from_front + cube_size / 2;
notch_z0 = max(F, F + usb_z - 5);

function g(z) = (z - F) * tan(taper);  // how much the cavity has flared at height z

// Cavity cross-section at the floor
module base2d() {
    if (pot_shape == "box")
        offset(r = box_corner_r) square([2 * fence_hx + 1, 2 * fence_hy + 1], center = true);
    else
        circle(r = sqrt(fence_hx * fence_hx + fence_hy * fence_hy) + 0.5);
}

// Thin slice of the flared shape at height z, grown outward by `grow`
module sec(z, grow) {
    translate([0, 0, z]) linear_extrude(0.01) offset(r = g(z) + grow) base2d();
}

module cavity() { hull() { sec(F, 0); sec(H + 1, 0); } }

module notch() {
    hull() {
        translate([0, 0, notch_z0 + notch_w / 2]) rotate([-90, 0, 0]) cylinder(d = notch_w, h = 200);
        translate([-notch_w / 2, 0, H - 1]) cube([notch_w, 200, 10]);
    }
}

module ledge() {
    difference() {
        hull() { sec(z_ledge - ledge_w, 0.5); sec(z_ledge, 0.5); }
        hull() { sec(z_ledge - ledge_w - 0.01, 0); sec(z_ledge + 0.01, -ledge_w); }
    }
}

module fence() {
    difference() {
        translate([0, 0, F - 0.01]) linear_extrude(fence_h)
            difference() {
                square([2 * fence_hx, 2 * fence_hy], center = true);
                square([2 * (fence_hx - fence_t), 2 * (fence_hy - fence_t)], center = true);
            }
        translate([-notch_w / 2, 0, F - 1]) cube([notch_w, 100, 100]);
    }
}

module pot() {
    difference() {
        union() {
            hull() { sec(0, wall); sec(H - rim_h, wall); }
            hull() {
                sec(H - rim_h - rim_lip, wall);       // 45-degree underside: prints without support
                sec(H - rim_h, wall + rim_lip);
                sec(H, wall + rim_lip);
            }
        }
        cavity();
        notch();
        translate([0, 0, -1]) cylinder(d = push_hole_d, h = F + 2);
    }
    difference() {
        union() { ledge(); fence(); }
        notch();
    }
}

module deck2d() {
    difference() {
        offset(r = g(z_ledge) - 0.4) base2d();
        translate([cube_x_offset, cube_yc]) square(cube_size + 2 * deck_clear, center = true);
        if (deck_holes)
            for (x = [-60:7:60], y = [-60:7:60])
                if (abs(x - cube_x_offset) > cube_size / 2 + deck_clear + 3 ||
                    abs(y - cube_yc) > cube_size / 2 + deck_clear + 3)
                    translate([x, y]) circle(d = 2.5, $fn = 16);
    }
}

module deck() { linear_extrude(deck_t) deck2d(); }

// ---------- preview helpers (not for printing) ----------
module ghost_case() {
    color("#1c1c1c") translate([-case_w / 2, -case_d / 2, F]) cube([case_w, case_d, case_h]);
    color("#9fb4ff", 0.45)
        translate([cube_x_offset - cube_size / 2, cube_yc - cube_size / 2, z_case_top - 5])
            cube([cube_size, cube_size, cube_size]);
}

module moss() {
    r_out = g(z_deck_top) + sqrt(fence_hx * fence_hx + fence_hy * fence_hy);
    pts = rands(0, 1, 600, 42);
    color("#5f7f3c") intersection() {
        hull() { sec(z_deck_top - 1, -0.5); sec(H + 10, -0.5); }
        for (i = [0:3:597]) {
            a = pts[i] * 360;
            rr = sqrt(pts[i + 1]) * (r_out - 3);
            x = rr * cos(a);
            y = rr * sin(a);
            if (abs(x - cube_x_offset) > cube_size / 2 + 2.5 || abs(y - cube_yc) > cube_size / 2 + 2.5)
                translate([x, y, z_deck_top]) scale([1, 1, 0.7]) sphere(r = 2.5 + pts[i + 2] * 2.5, $fn = 12);
        }
    }
}

module cut() {
    if (part == "section") difference() { children(); translate([0, -150, -1]) cube([150, 300, 200]); }
    else children();
}

module assembled() {
    cut() color("#c4673f") pot();
    cut() color("#7a5a3a") translate([0, 0, z_ledge]) deck();
    cut() ghost_case();
    cut() moss();
}

if (part == "pot") pot();
else if (part == "deck") deck();
else assembled();

rim_r = g(H) + wall + rim_lip;
rim_size = pot_shape == "box"
    ? [2 * (fence_hx + 0.5 + box_corner_r + rim_r), 2 * (fence_hy + 0.5 + box_corner_r + rim_r)]
    : [2 * (sqrt(fence_hx * fence_hx + fence_hy * fence_hy) + 0.5 + rim_r), 0];
echo(str("Pot ", H, " mm tall; rim ", rim_size[0], pot_shape == "box" ? str(" x ", rim_size[1]) : " diameter",
         " mm; cube shows ", F + 58 - H, " mm above the rim"));
