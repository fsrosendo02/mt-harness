    public Map<String, DateTimeZone> compile(File outputDir, File[] sources) throws IOException {
        if (sources != null) {
            for (int i=0; i<sources.length; i++) {
                BufferedReader in = new BufferedReader(new FileReader(sources[i]));
                parseDataFile(in);
                in.close();
            }
        }

        if (outputDir != null) {
            if (!outputDir.exists()) {
                throw new IOException("Destination directory doesn't exist: " + outputDir);
            }
            if (!outputDir.isDirectory()) {
                throw new IOException("Destination is not a directory: " + outputDir);
            }
        }

        Map<String, DateTimeZone> map = new TreeMap<String, DateTimeZone>();

        System.out.println("Writing zoneinfo files");
        for (int i=0; i<iZones.size(); i++) {
            Zone zone = iZones.get(i);
            DateTimeZoneBuilder builder = new DateTimeZoneBuilder();
            zone.addToBuilder(builder, iRuleSets);
            final DateTimeZone original = builder.toDateTimeZone(zone.iName, true);
            DateTimeZone tz = original;
            if (test(tz.getID(), tz)) {
                map.put(tz.getID(), tz);
                if (outputDir != null) {
                    if (ZoneInfoCompiler.verbose()) {
                        System.out.println("Writing " + tz.getID());
                    }
                    File file = new File(outputDir, tz.getID());
                    if (!file.getParentFile().exists()) {
                        file.getParentFile().mkdirs();
                    }
                    OutputStream out = new FileOutputStream(file);
                    builder.writeTo(zone.iName, out);
                    out.close();

                    // Test if it can be read back.
                    InputStream in = new FileInputStream(file);
                    DateTimeZone tz2 = DateTimeZoneBuilder.readFrom(in, tz.getID());
                    in.close();

                    if (!original.equals(tz2)) {
                        System.out.println("*e* Error in " + tz.getID() +
                                           ": Didn't read properly from file");
                    }
                }
            }
        }

        for (int pass=0; pass<2; pass++) {
            for (int i=0; i<iLinks.size(); i += 2) {
                String id = iLinks.get(i);
                String alias = iLinks.get(i + 1);
                DateTimeZone tz = map.get(id);
                if (tz == null) {
                    if (pass > 0) {
                        System.out.println("Cannot find time zone '" + id +
                                           "' to link alias '" + alias + "' to");
                    }
                } else {
                    map.put(alias, tz);
                }
            }
        }

        if (outputDir != null) {
            System.out.println("Writing ZoneInfoMap");
            File file = new File(outputDir, "ZoneInfoMap");
            if (!file.getParentFile().exists()) {
                file.getParentFile().mkdirs();
            }

            OutputStream out = new FileOutputStream(file);
            DataOutputStream dout = new DataOutputStream(out);
            // Sort and filter out any duplicates that match case.
            Map<String, DateTimeZone> zimap = new TreeMap<String, DateTimeZone>(String.CASE_INSENSITIVE_ORDER);
            zimap.putAll(map);
            writeZoneInfoMap(dout, zimap);
            dout.close();
        }

        return map;
    }
