import os
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Brand Traders Analytics", page_icon="📊", layout="wide")

TABLES = ["categories", "customers", "employees", "orders",
          "order_details", "products", "shippers"]
SEARCH_DIRS = ["data", "."]


# ------------------------------------------------------------------ loading
def read_csv_safe(src):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            if hasattr(src, "seek"):
                src.seek(0)
            return pd.read_csv(src, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode file")


def load_tables(uploads):
    tables = {}
    if uploads:
        for f in uploads:
            name = os.path.splitext(f.name)[0].lower()
            if name in TABLES:
                tables[name] = read_csv_safe(f)
    for name in TABLES:
        if name in tables:
            continue
        for d in SEARCH_DIRS:
            path = os.path.join(d, f"{name}.csv")
            if os.path.exists(path):
                tables[name] = read_csv_safe(path)
                break
    return tables


@st.cache_data(show_spinner="Joining tables...")
def build_master(t):
    cats = t["categories"][["categoryID", "categoryName"]]
    prod = (t["products"]
            .rename(columns={"unitPrice": "listPrice"})
            .merge(cats, on="categoryID", how="left")
            [["productID", "productName", "listPrice", "discontinued",
              "categoryID", "categoryName"]])
    cust = (t["customers"]
            .rename(columns={"companyName": "customerName",
                             "city": "customerCity",
                             "country": "customerCountry"})
            [["customerID", "customerName", "customerCity", "customerCountry"]])
    emp = (t["employees"]
           .rename(columns={"title": "employeeTitle"})
           [["employeeID", "employeeName", "employeeTitle"]])
    ship = t["shippers"].rename(columns={"companyName": "shipperName"})

    o = t["orders"].copy()
    for c in ["orderDate", "requiredDate", "shippedDate"]:
        o[c] = pd.to_datetime(o[c], errors="coerce")

    df = (t["order_details"]
          .merge(o, on="orderID", how="left")
          .merge(prod, on="productID", how="left")
          .merge(cust, on="customerID", how="left")
          .merge(emp, on="employeeID", how="left")
          .merge(ship, on="shipperID", how="left"))

    df["gross"] = df["unitPrice"] * df["quantity"]
    df["discountAmt"] = df["gross"] * df["discount"]
    df["revenue"] = df["gross"] - df["discountAmt"]
    df["year"] = df["orderDate"].dt.year
    df["month"] = df["orderDate"].dt.to_period("M").dt.to_timestamp()
    df["weekday"] = df["orderDate"].dt.day_name()
    return df


def order_level(f):
    """One row per order (freight lives at order level, so never sum it on lines)."""
    od = (f.groupby("orderID")
          .agg(orderDate=("orderDate", "first"),
               requiredDate=("requiredDate", "first"),
               shippedDate=("shippedDate", "first"),
               customerName=("customerName", "first"),
               customerCountry=("customerCountry", "first"),
               employeeName=("employeeName", "first"),
               shipperName=("shipperName", "first"),
               freight=("freight", "first"),
               units=("quantity", "sum"),
               revenue=("revenue", "sum"))
          .reset_index())
    od["daysToShip"] = (od["shippedDate"] - od["orderDate"]).dt.days
    od["late"] = od["shippedDate"] > od["requiredDate"]
    od["shipped"] = od["shippedDate"].notna()
    return od


def show(fig):
    fig.update_layout(margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, width="stretch")


def money(x):
    return f"${x:,.0f}"


# ------------------------------------------------------------------ sidebar
st.sidebar.title("📊 Brand Traders")
uploads = st.sidebar.file_uploader(
    "Upload the 7 CSV files (or place them in ./data)",
    type="csv", accept_multiple_files=True)

tables = load_tables(uploads)
missing = [n for n in TABLES if n not in tables]
if missing:
    st.title("Brand Traders Sales Analytics")
    st.warning(f"Missing tables: {', '.join(missing)}. Upload them in the sidebar "
               "or put them in a `data/` folder next to app.py.")
    st.stop()

master = build_master(tables)

st.sidebar.header("Filters")
dmin, dmax = master["orderDate"].min().date(), master["orderDate"].max().date()
date_range = st.sidebar.date_input("Order date range", (dmin, dmax),
                                   min_value=dmin, max_value=dmax)
if len(date_range) != 2:
    st.stop()


def multi(label, col):
    return st.sidebar.multiselect(label, sorted(master[col].dropna().unique()))


sel_country = multi("Customer country", "customerCountry")
sel_cat = multi("Category", "categoryName")
sel_emp = multi("Employee", "employeeName")
sel_ship = multi("Shipper", "shipperName")
top_n = st.sidebar.slider("Top N for rankings", 5, 25, 10)

f = master[(master["orderDate"].dt.date >= date_range[0]) &
           (master["orderDate"].dt.date <= date_range[1])]
for col, sel in [("customerCountry", sel_country), ("categoryName", sel_cat),
                 ("employeeName", sel_emp), ("shipperName", sel_ship)]:
    if sel:
        f = f[f[col].isin(sel)]

if f.empty:
    st.error("No data for the selected filters.")
    st.stop()

od = order_level(f)

# ------------------------------------------------------------------ header KPIs
st.title("Brand Traders Sales Analytics")
k = st.columns(6)
k[0].metric("Revenue", money(f["revenue"].sum()))
k[1].metric("Orders", f"{od.shape[0]:,}")
k[2].metric("Customers", f"{f['customerID'].nunique():,}")
k[3].metric("Avg order value", money(od["revenue"].mean()))
k[4].metric("Units sold", f"{int(f['quantity'].sum()):,}")
k[5].metric("Effective discount", f"{f['discountAmt'].sum() / f['gross'].sum():.1%}")

tabs = st.tabs(["Overview", "Products", "Customers", "Employees",
                "Shipping", "Data & Quality"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    monthly = f.groupby("month", as_index=False).agg(
        revenue=("revenue", "sum"), orders=("orderID", "nunique"))
    monthly["MoM %"] = monthly["revenue"].pct_change() * 100
    c1, c2 = st.columns([2, 1])
    with c1:
        show(px.line(monthly, x="month", y="revenue", markers=True,
                     title="Monthly revenue"))
    with c2:
        show(px.bar(monthly, x="month", y="orders", title="Monthly orders"))

    c3, c4 = st.columns(2)
    with c3:
        yearly = f.groupby("year", as_index=False).agg(
            revenue=("revenue", "sum"), months=("month", "nunique"))
        yearly["avg monthly revenue"] = yearly["revenue"] / yearly["months"]
        show(px.bar(yearly, x="year", y="avg monthly revenue",
                    title="Average monthly revenue by year (fair for partial years)",
                    text_auto=".3s"))
    with c4:
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]
        wd = (f.groupby("weekday")["revenue"].sum().reindex(order).reset_index())
        show(px.bar(wd, x="weekday", y="revenue", title="Revenue by weekday"))

    f2 = f.copy()
    f2["mon"] = f2["orderDate"].dt.month_name().str[:3]
    heat = f2.pivot_table(index="year", columns="mon", values="revenue", aggfunc="sum")
    heat = heat.reindex(columns=[m for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                                 if m in heat.columns])
    show(px.imshow(heat, text_auto=".2s", aspect="auto", color_continuous_scale="Blues",
                   title="Revenue heatmap: year by month"))
    with st.expander("Monthly table"):
        st.dataframe(monthly.round(2), width="stretch")

# ------------------------------------------------------------------ products
with tabs[1]:
    c1, c2 = st.columns(2)
    cat = f.groupby("categoryName", as_index=False)["revenue"].sum()
    with c1:
        show(px.pie(cat, names="categoryName", values="revenue", hole=0.45,
                    title="Revenue share by category"))
    with c2:
        prod = (f.groupby("productName", as_index=False)
                .agg(revenue=("revenue", "sum"), units=("quantity", "sum"))
                .sort_values("revenue", ascending=False))
        show(px.bar(prod.head(top_n).sort_values("revenue"), x="revenue",
                    y="productName", orientation="h",
                    title=f"Top {top_n} products by revenue"))

    show(px.sunburst(f, path=["categoryName", "productName"], values="revenue",
                     title="Category to product drill down"))

    c3, c4 = st.columns(2)
    with c3:
        bins = pd.cut(f["discount"], [-0.001, 0, 0.05, 0.10, 0.15, 0.20, 1],
                      labels=["0%", "1-5%", "6-10%", "11-15%", "16-20%", ">20%"])
        d = (f.groupby(bins, observed=True)
             .agg(avg_qty=("quantity", "mean"), lines=("orderID", "count"))
             .reset_index())
        d.columns = ["discount band", "avg quantity per line", "order lines"]
        show(px.bar(d, x="discount band", y="avg quantity per line",
                    hover_data=["order lines"],
                    title="Does discounting lift quantity per line?"))
    with c4:
        show(px.scatter(prod, x="units", y="revenue", hover_name="productName",
                        title="Units vs revenue per product"))

    disc = f.groupby("discontinued")["revenue"].sum()
    st.caption(f"Revenue from discontinued products: "
               f"{money(disc.get(1, 0))} ({disc.get(1, 0) / disc.sum():.1%} of total)")
    st.subheader("Product performance table")
    st.dataframe(prod.round(2), width="stretch", hide_index=True)

# ------------------------------------------------------------------ customers
with tabs[2]:
    cust = (f.groupby(["customerID", "customerName", "customerCountry"], as_index=False)
            .agg(revenue=("revenue", "sum"), orders=("orderID", "nunique"),
                 last_order=("orderDate", "max")))
    cust["avg order value"] = cust["revenue"] / cust["orders"]
    cust["days since last order"] = (f["orderDate"].max() - cust["last_order"]).dt.days
    cust = cust.sort_values("revenue", ascending=False)

    c1, c2 = st.columns(2)
    with c1:
        show(px.bar(cust.head(top_n).sort_values("revenue"), x="revenue",
                    y="customerName", orientation="h",
                    title=f"Top {top_n} customers"))
    with c2:
        ctry = f.groupby("customerCountry", as_index=False)["revenue"].sum()
        show(px.choropleth(ctry, locations="customerCountry",
                           locationmode="country names", color="revenue",
                           color_continuous_scale="Blues",
                           title="Revenue by customer country"))

    c3, c4 = st.columns(2)
    with c3:
        p = cust.reset_index(drop=True)
        p["cum share"] = p["revenue"].cumsum() / p["revenue"].sum()
        p["rank"] = np.arange(1, len(p) + 1)
        show(px.line(p, x="rank", y="cum share",
                     title="Pareto: cumulative revenue share by customer rank"))
        top20 = p.head(max(1, round(len(p) * 0.2)))["revenue"].sum() / p["revenue"].sum()
        st.caption(f"Top 20% of customers generate {top20:.0%} of revenue.")
    with c4:
        cc = (f.groupby("customerCountry")
              .agg(revenue=("revenue", "sum"), customers=("customerID", "nunique"))
              .assign(rev_per_customer=lambda x: x["revenue"] / x["customers"])
              .sort_values("rev_per_customer", ascending=False).head(top_n)
              .reset_index())
        show(px.bar(cc, x="customerCountry", y="rev_per_customer",
                    title="Revenue per customer by country"))

    st.subheader("Customer table (recency view)")
    st.dataframe(cust.round(2), width="stretch", hide_index=True)

# ------------------------------------------------------------------ employees
with tabs[3]:
    e = (f.groupby(["employeeName"], as_index=False)
         .agg(revenue=("revenue", "sum"), orders=("orderID", "nunique"),
              customers=("customerID", "nunique")))
    e["avg order value"] = e["revenue"] / e["orders"]
    e = e.sort_values("revenue", ascending=False)
    c1, c2 = st.columns(2)
    with c1:
        show(px.bar(e, x="employeeName", y="revenue", title="Revenue by employee",
                    text_auto=".3s"))
    with c2:
        show(px.scatter(e, x="orders", y="avg order value", size="revenue",
                        hover_name="employeeName",
                        title="Volume vs order value (bubble = revenue)"))
    emp_month = f.groupby(["month", "employeeName"], as_index=False)["revenue"].sum()
    show(px.line(emp_month, x="month", y="revenue", color="employeeName",
                 title="Monthly revenue by employee"))
    st.dataframe(e.round(2), width="stretch", hide_index=True)

    hier = tables["employees"].merge(
        tables["employees"][["employeeID", "employeeName"]]
        .rename(columns={"employeeID": "reportsTo", "employeeName": "manager"}),
        on="reportsTo", how="left")[["employeeName", "title", "manager"]]
    with st.expander("Reporting structure"):
        st.dataframe(hier, width="stretch", hide_index=True)

# ------------------------------------------------------------------ shipping
with tabs[4]:
    shipped = od[od["shipped"]]
    s = (od.groupby("shipperName")
         .agg(orders=("orderID", "count"), freight=("freight", "sum"),
              avg_freight=("freight", "mean"))
         .join(shipped.groupby("shipperName").agg(
             avg_days_to_ship=("daysToShip", "mean"), late_rate=("late", "mean")))
         .reset_index())
    k2 = st.columns(4)
    k2[0].metric("Total freight", money(od["freight"].sum()))
    k2[1].metric("Freight % of revenue", f"{od['freight'].sum() / od['revenue'].sum():.1%}")
    k2[2].metric("Avg days to ship", f"{shipped['daysToShip'].mean():.1f}")
    k2[3].metric("Late shipments", f"{shipped['late'].mean():.1%}",
                 help=f"{int((~od['shipped']).sum())} orders have no shipped date yet")

    c1, c2 = st.columns(2)
    with c1:
        show(px.bar(s, x="shipperName", y="orders", title="Orders by shipper"))
    with c2:
        show(px.bar(s, x="shipperName", y="avg_days_to_ship",
                    title="Average days to ship, by shipper"))
    c3, c4 = st.columns(2)
    with c3:
        show(px.bar(s, x="shipperName", y="late_rate", title="Late rate by shipper",
                    text_auto=".1%"))
    with c4:
        fc = (od.groupby("customerCountry")
              .agg(freight=("freight", "sum"), revenue=("revenue", "sum"))
              .assign(freight_pct=lambda x: x["freight"] / x["revenue"])
              .sort_values("freight_pct", ascending=False).head(top_n).reset_index())
        show(px.bar(fc, x="customerCountry", y="freight_pct",
                    title="Freight as % of revenue, highest countries",
                    text_auto=".1%"))
    st.dataframe(s.round(3), width="stretch", hide_index=True)

# ------------------------------------------------------------------ data & quality
with tabs[5]:
    st.subheader("Table row counts")
    st.dataframe(pd.DataFrame({"table": list(tables),
                               "rows": [len(v) for v in tables.values()],
                               "columns": [v.shape[1] for v in tables.values()]}),
                 width="stretch", hide_index=True)

    st.subheader("Referential integrity checks (should all be 0)")
    t = tables
    checks = {
        "orders without customer": (~t["orders"]["customerID"].isin(t["customers"]["customerID"])).sum(),
        "orders without employee": (~t["orders"]["employeeID"].isin(t["employees"]["employeeID"])).sum(),
        "orders without shipper": (~t["orders"]["shipperID"].isin(t["shippers"]["shipperID"])).sum(),
        "lines without order": (~t["order_details"]["orderID"].isin(t["orders"]["orderID"])).sum(),
        "lines without product": (~t["order_details"]["productID"].isin(t["products"]["productID"])).sum(),
        "products without category": (~t["products"]["categoryID"].isin(t["categories"]["categoryID"])).sum(),
    }
    st.dataframe(pd.DataFrame(checks.items(), columns=["check", "violations"]),
                 width="stretch", hide_index=True)
    st.caption("Revenue = unitPrice (from order_details) x quantity x (1 - discount). "
               "Freight is taken once per order to avoid double counting.")

    st.subheader("Joined dataset (filtered)")
    st.dataframe(f.drop(columns=["month"]).head(500), width="stretch")
    st.download_button("Download filtered data as CSV",
                       f.to_csv(index=False).encode("utf-8"),
                       "brand_traders_filtered.csv", "text/csv")
