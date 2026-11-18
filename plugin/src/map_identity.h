/* map_identity.h -- the plugin's identity: names, bundle identifiers, AU codes, VST 3 class IDs (#22).
 *
 * SINGLE SOURCE OF TRUTH. CMakeLists.txt reads the MAP_* defines below with file(STRINGS) to fill the bundles'
 * Info.plist files, and the C++ wrappers include this header. Change a value here only.
 *
 * TEMPORARY. #75 has not chosen a product name, so everything here is a placeholder. All of it will change once a
 * name is picked. Hosts cache the AU type/subtype/manufacturer and the VST 3 class IDs, so a plugin with new IDs
 * shows up as a new plugin: old sessions will not find it. No brand or device names, by rule.
 *
 * Format: one "#define MAP_NAME value" per line; strings in double quotes, no escaped quotes, no line continuation.
 */
#ifndef MAP_IDENTITY_H
#define MAP_IDENTITY_H

#define MAP_PRODUCT_NAME "MAP Valve Stage (temporary)"
#define MAP_VENDOR_NAME "MAP project (temporary)"
#define MAP_VENDOR_URL ""
#define MAP_VENDOR_EMAIL ""
#define MAP_VERSION_STRING "0.1.0"
/* AU component version, 0xMMMMmmbb: 0.1.0 */
#define MAP_AU_VERSION_INT 256

/* Bundle identifiers: "local." because nothing is published or signed with a real identity. */
#define MAP_VST3_BUNDLE_ID "local.map.valvestage-temporary.vst3"
#define MAP_AU_BUNDLE_ID "local.map.valvestage-temporary.component"
#define MAP_BUNDLE_NAME "MAP Valve Stage (temporary)"
/* file name of both bundles (.vst3, .component) and of the AU executable: no spaces or parentheses */
#define MAP_BUNDLE_FILE "MAPValveStage-temporary"

/* AudioUnit: type aufx (effect). Four-character codes; Apple reserves all-lowercase manufacturer codes. */
#define MAP_AU_TYPE "aufx"
#define MAP_AU_SUBTYPE "vls1"
#define MAP_AU_MANUFACTURER "MapT"
#define MAP_AU_FACTORY "MapValveStageAUFactory"

/* VST 3 class IDs (uuidgen, 18 Nov 2026), as four 32-bit words */
#define MAP_VST3_PROCESSOR_UID 0xE74295E2, 0xAF18482C, 0x9E263991, 0xBB1E35DB
#define MAP_VST3_CONTROLLER_UID 0xAE939A30, 0xD6384E4B, 0xB4057A9C, 0x71D8AA2D
#define MAP_VST3_CATEGORY "Fx|Distortion"

#endif
