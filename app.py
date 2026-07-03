from flask import Flask, render_template, request, redirect, url_for, flash
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

app = Flask(__name__)
app.secret_key = "oem_erp_secret_key"  # 用于消息提示


# ===================== 原有ERP核心类（完全复用） =====================
class Material:
    def __init__(self, name: str, material_type: str, unit: str, unit_price: float,
                 shelf_life_days: int, supplier: str = ""):
        self.material_id = str(uuid.uuid4())[:8]
        self.name = name
        self.type = material_type
        self.unit = unit
        self.unit_price = unit_price
        self.shelf_life_days = shelf_life_days
        self.supplier = supplier


class BOM:
    def __init__(self, product_name: str, customer: str, formula_desc: str,
                 materials: Dict[str, float]):
        self.bom_id = str(uuid.uuid4())[:8]
        self.product_name = product_name
        self.customer = customer
        self.formula_desc = formula_desc
        self.materials = materials


class CustomerOrder:
    def __init__(self, customer_name: str, product_name: str, quantity: int,
                 delivery_date: str, bom_id: str):
        self.order_id = str(uuid.uuid4())[:8]
        self.customer_name = customer_name
        self.product_name = product_name
        self.quantity = quantity
        self.delivery_date = delivery_date
        self.bom_id = bom_id
        self.status = "待生产"


class WorkOrder:
    def __init__(self, order_id: str, product_name: str, quantity: int, bom_id: str):
        self.wo_id = str(uuid.uuid4())[:8]
        self.order_id = order_id
        self.product_name = product_name
        self.produce_qty = quantity
        self.bom_id = bom_id
        self.batch_no = f"BP{datetime.now().strftime('%Y%m%d')}{str(uuid.uuid4())[:4].upper()}"
        self.produce_date = datetime.now().strftime("%Y-%m-%d")
        self.status = "待领料"


class InventoryManager:
    def __init__(self):
        self.stock: Dict[str, List[Dict]] = {}

    def stock_in(self, material_id: str, quantity: float, batch_no: str,
                 produce_date: str, shelf_life_days: int = 365) -> None:
        if material_id not in self.stock:
            self.stock[material_id] = []
        exp_date = (datetime.strptime(produce_date, "%Y-%m-%d") + timedelta(days=shelf_life_days)).strftime("%Y-%m-%d")
        self.stock[material_id].append({
            "batch_no": batch_no,
            "quantity": quantity,
            "produce_date": produce_date,
            "expire_date": exp_date
        })

    def stock_out(self, material_id: str, quantity: float) -> bool:
        if material_id not in self.stock or self.get_total_stock(material_id) < quantity:
            return False
        need_qty = quantity
        for batch in sorted(self.stock[material_id], key=lambda x: x["produce_date"]):
            if need_qty <= 0:
                break
            if batch["quantity"] >= need_qty:
                batch["quantity"] -= need_qty
                need_qty = 0
            else:
                need_qty -= batch["quantity"]
                batch["quantity"] = 0
        self.stock[material_id] = [b for b in self.stock[material_id] if b["quantity"] > 0]
        return True

    def get_total_stock(self, material_id: str) -> float:
        if material_id not in self.stock:
            return 0.0
        return sum(b["quantity"] for b in self.stock[material_id])


class FoodOEMERP:
    def __init__(self):
        self.materials: Dict[str, Material] = {}
        self.boms: Dict[str, BOM] = {}
        self.orders: Dict[str, CustomerOrder] = {}
        self.work_orders: Dict[str, WorkOrder] = {}
        self.inventory = InventoryManager()

    def add_material(self, name, m_type, unit, price, shelf_life, supplier=""):
        mat = Material(name, m_type, unit, price, shelf_life, supplier)
        self.materials[mat.material_id] = mat
        return mat

    def create_bom(self, product_name, customer, formula_desc, materials_qty):
        bom = BOM(product_name, customer, formula_desc, materials_qty)
        self.boms[bom.bom_id] = bom
        return bom

    def create_order(self, customer, product, qty, delivery, bom_id):
        if bom_id not in self.boms:
            return None
        order = CustomerOrder(customer, product, qty, delivery, bom_id)
        self.orders[order.order_id] = order
        return order

    def create_work_order(self, order_id):
        order = self.orders.get(order_id)
        if not order:
            return None
        wo = WorkOrder(order_id, order.product_name, order.quantity, order.bom_id)
        self.work_orders[wo.wo_id] = wo
        order.status = "生产中"
        return wo

    def pick_materials(self, wo_id):
        wo = self.work_orders.get(wo_id)
        bom = self.boms.get(wo.bom_id)
        if not wo or not bom:
            return False
        for mat_id, per_qty in bom.materials.items():
            total_need = per_qty * wo.produce_qty
            if not self.inventory.stock_out(mat_id, total_need):
                return False
        wo.status = "生产中"
        return True

    def finish_production(self, wo_id):
        wo = self.work_orders.get(wo_id)
        order = self.orders.get(wo.order_id)
        if not wo:
            return False
        self.inventory.stock_in(
            material_id=f"FIN_{wo.product_name}",
            quantity=wo.produce_qty,
            batch_no=wo.batch_no,
            produce_date=wo.produce_date
        )
        wo.status = "已完工"
        order.status = "已完工"
        return True

    def deliver_order(self, order_id):
        order = self.orders.get(order_id)
        if not order or order.status != "已完工":
            return False
        success = self.inventory.stock_out(f"FIN_{order.product_name}", order.quantity)
        if success:
            order.status = "已发货"
        return success

    def calculate_order_cost(self, order_id):
        order = self.orders.get(order_id)
        if not order:
            return {}
        bom = self.boms.get(order.bom_id)
        unit_cost = 0.0
        for mat_id, qty in bom.materials.items():
            mat = self.materials.get(mat_id)
            if mat:
                unit_cost += mat.unit_price * qty
        total_material_cost = unit_cost * order.quantity
        return {
            "订单号": order_id,
            "产品": order.product_name,
            "数量": order.quantity,
            "单位原料成本": round(unit_cost, 2),
            "原料总成本": round(total_material_cost, 2),
            "预估总成本(含30%制造费)": round(total_material_cost * 1.3, 2)
        }


# 全局ERP实例（原型内存存储，重启后数据清空）
erp = FoodOEMERP()


# ===================== Web路由控制 =====================
@app.route('/')
def index():
    # 仪表盘统计数据
    stats = {
        "物料总数": len(erp.materials),
        "BOM配方数": len(erp.boms),
        "订单总数": len(erp.orders),
        "工单总数": len(erp.work_orders)
    }
    return render_template('index.html', stats=stats)


@app.route('/materials', methods=['GET', 'POST'])
def materials():
    if request.method == 'POST':
        # 新增物料
        name = request.form['name']
        m_type = request.form['type']
        unit = request.form['unit']
        price = float(request.form['price'])
        shelf_life = int(request.form['shelf_life'])
        supplier = request.form['supplier']
        erp.add_material(name, m_type, unit, price, shelf_life, supplier)
        flash('物料添加成功', 'success')
        return redirect(url_for('materials'))

    # 物料入库操作
    if request.args.get('action') == 'stock_in':
        mat_id = request.args.get('mat_id')
        qty = float(request.args.get('qty'))
        batch = request.args.get('batch')
        date = request.args.get('date')
        mat = erp.materials.get(mat_id)
        erp.inventory.stock_in(mat_id, qty, batch, date, mat.shelf_life_days)
        flash('原料入库成功', 'success')
        return redirect(url_for('materials'))

    material_list = list(erp.materials.values())
    return render_template('materials.html', materials=material_list)


@app.route('/boms', methods=['GET', 'POST'])
def boms():
    if request.method == 'POST':
        product_name = request.form['product_name']
        customer = request.form['customer']
        formula_desc = request.form['formula_desc']
        # 解析物料配方：格式 物料ID1:用量1,物料ID2:用量2
        materials_str = request.form['materials']
        materials_dict = {}
        for item in materials_str.split(','):
            mat_id, qty = item.strip().split(':')
            materials_dict[mat_id.strip()] = float(qty.strip())
        erp.create_bom(product_name, customer, formula_desc, materials_dict)
        flash('BOM配方创建成功', 'success')
        return redirect(url_for('boms'))

    bom_list = list(erp.boms.values())
    material_list = list(erp.materials.values())
    return render_template('boms.html', boms=bom_list, materials=material_list)


@app.route('/orders', methods=['GET', 'POST'])
def orders():
    if request.method == 'POST':
        customer = request.form['customer_name']
        product = request.form['product_name']
        qty = int(request.form['quantity'])
        delivery = request.form['delivery_date']
        bom_id = request.form['bom_id']
        erp.create_order(customer, product, qty, delivery, bom_id)
        flash('客户订单创建成功', 'success')
        return redirect(url_for('orders'))

    order_list = list(erp.orders.values())
    bom_list = list(erp.boms.values())
    return render_template('orders.html', orders=order_list, boms=bom_list)


@app.route('/orders/<order_id>/create_wo')
def create_work_order(order_id):
    wo = erp.create_work_order(order_id)
    if wo:
        flash('生产工单已生成', 'success')
    else:
        flash('工单生成失败', 'danger')
    return redirect(url_for('workorders'))


@app.route('/orders/<order_id>/deliver')
def deliver_order(order_id):
    success = erp.deliver_order(order_id)
    if success:
        flash('订单已发货', 'success')
    else:
        flash('发货失败：订单未完工或库存不足', 'danger')
    return redirect(url_for('orders'))


@app.route('/workorders')
def workorders():
    wo_list = list(erp.work_orders.values())
    return render_template('workorders.html', workorders=wo_list)


@app.route('/workorders/<wo_id>/pick')
def pick_materials(wo_id):
    success = erp.pick_materials(wo_id)
    if success:
        flash('生产领料完成', 'success')
    else:
        flash('领料失败：库存不足', 'danger')
    return redirect(url_for('workorders'))


@app.route('/workorders/<wo_id>/finish')
def finish_production(wo_id):
    success = erp.finish_production(wo_id)
    if success:
        flash('生产完工，成品已入库', 'success')
    else:
        flash('完工失败', 'danger')
    return redirect(url_for('workorders'))


@app.route('/inventory')
def inventory():
    stock_data = []
    # 原料库存
    for mat_id, mat in erp.materials.items():
        total = erp.inventory.get_total_stock(mat_id)
        batches = erp.inventory.stock.get(mat_id, [])
        stock_data.append({
            "id": mat_id,
            "name": mat.name,
            "type": mat.type,
            "unit": mat.unit,
            "total": total,
            "batches": batches
        })
    # 成品库存
    for key, batches in erp.inventory.stock.items():
        if key.startswith("FIN_"):
            product_name = key.replace("FIN_", "")
            total = sum(b["quantity"] for b in batches)
            stock_data.append({
                "id": key,
                "name": f"[成品] {product_name}",
                "type": "成品",
                "unit": "瓶/盒",
                "total": total,
                "batches": batches
            })
    return render_template('inventory.html', stock_data=stock_data)


@app.route('/cost', methods=['GET', 'POST'])
def cost():
    cost_result = None
    selected_order = None
    if request.method == 'POST':
        order_id = request.form['order_id']
        cost_result = erp.calculate_order_cost(order_id)
        selected_order = order_id
    order_list = list(erp.orders.values())
    return render_template('cost.html', orders=order_list, cost_result=cost_result, selected=selected_order)


if __name__ == '__main__':
    # 核心修复：关闭debug模式，解决IPython环境端口冲突
    app.run(debug=False, host='0.0.0.0', port=8080)