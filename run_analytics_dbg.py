import sys
import traceback
sys.path.insert(0, r"D:/Jester/Jester/python")
from jester.agents import property_analytics

DB = r"D:/Jester/Jester/go/data/jester.db"

try:
    results = property_analytics.run_full_analytics(DB)
    print("=== SUCCESS ===")
    print("total_listings:", results["summary"]["total_listings"])
    print("portals:", results["summary"]["portals"])
    print("by_city stats count:", len(results["price_stats"]["by_city"]))
    print("listing_analytics count:", len(results["listing_analytics"]))
    print("duplicates:", len(results["duplicates"]))
    print("suspicious:", len(results["suspicious"]))
    print("heatmap:", len(results["heatmap"]))
except Exception:
    print("=== FAILURE ===")
    traceback.print_exc()
