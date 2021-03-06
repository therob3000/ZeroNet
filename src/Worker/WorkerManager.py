from Worker import Worker
import gevent, time, logging

MAX_WORKERS = 10

# Worker manager for site
class WorkerManager:
	def __init__(self, site):
		self.site = site
		self.workers = {} # Key: ip:port, Value: Worker.Worker
		self.tasks = [] # {"evt": evt, "workers_num": 0, "site": self.site, "inner_path": inner_path, "done": False, "time_start": time.time(), "peers": peers}
		self.log = logging.getLogger("WorkerManager:%s" % self.site.address_short)
		self.process_taskchecker = gevent.spawn(self.checkTasks)


	# Check expired tasks
	def checkTasks(self):
		while 1:
			time.sleep(15) # Check every 30 sec
			if not self.tasks: continue
			tasks = self.tasks[:] # Copy it so removing elements wont cause any problem
			for task in tasks:
				if time.time() >= task["time_start"]+60: # Task timed out
					self.log.debug("Cleaning up task: %s" % task)

					# Clean up workers
					workers = self.findWorkers(task)
					for worker in workers:
						worker.stop()

					# Remove task
					self.failTask(task)
				elif time.time() >= task["time_start"]+15: # Task taking long time
					self.log.debug("Task taking long time, find more peers: %s" % task["inner_path"])
					task["site"].announce() # Find more peers
					if task["peers"]: # Release the peer olck
						self.log.debug("Task peer lock release: %s" % task["inner_path"])
						task["peers"] = []
						self.startWorkers()
					continue # One reannounce per loop


	# Returns the next free or less worked task
	def getTask(self, peer, only_free=False):
		best_task = None
		for task in self.tasks: # Find out the task with lowest worker number
			if task["peers"] and peer not in task["peers"]: continue # This peer not allowed to pick this task
			if task["inner_path"] == "content.json": return task # Content.json always prority
			if not best_task or task["workers_num"] < best_task["workers_num"]: # If task has lower worker number then its better
				best_task = task
		return best_task


	# New peers added to site
	def onPeers(self):
		self.startWorkers()


	# Start workers to process tasks
	def startWorkers(self):
		if len(self.workers) >= MAX_WORKERS: return False # Workers number already maxed
		if not self.tasks: return False # No task for workers
		for key, peer in self.site.peers.iteritems(): # One worker for every peer
			if key not in self.workers and len(self.workers) < MAX_WORKERS: # We dont have worker for that peer and workers num less than max
				worker = Worker(self, peer)
				self.workers[key] = worker
				worker.key = key
				worker.start()
				self.log.debug("Added worker: %s, workers: %s/%s" % (key, len(self.workers), MAX_WORKERS))


	# Find workers by task
	def findWorkers(self, task):
		workers = []
		for worker in self.workers.values():
			if worker.task == task: workers.append(worker)
		return workers

	# Ends and remove a worker
	def removeWorker(self, worker):
		worker.running = False
		del(self.workers[worker.key])
		self.log.debug("Removed worker, workers: %s/%s" % (len(self.workers), MAX_WORKERS))


	# Create new task and return asyncresult
	def addTask(self, inner_path, peer=None):
		self.site.onFileStart(inner_path) # First task, trigger site download started
		task = self.findTask(inner_path)
		if task: # Already has task for that file
			if peer and task["peers"]: # This peer has new version too
				task["peers"].append(peer)
				self.startWorkers()
			return task["evt"]
		else: # No task for that file yet
			evt = gevent.event.AsyncResult()
			if peer:
				peers = [peer] # Only download from this peer
			else:
				peers = None
			task = {"evt": evt, "workers_num": 0, "site": self.site, "inner_path": inner_path, "done": False, "time_start": time.time(), "peers": peers}
			self.tasks.append(task)
			self.log.debug("New task: %s" % task)
			self.startWorkers()
			return evt


	# Find a task using inner_path
	def findTask(self, inner_path):
		for task in self.tasks:
			if task["inner_path"] == inner_path: 
				return task
		return None # Not found


	# Mark a task failed
	def failTask(self, task):
		task["done"] = True
		self.tasks.remove(task) # Remove from queue
		self.site.onFileFail(task["inner_path"])
		task["evt"].set(False)


	# Mark a task done
	def doneTask(self, task):
		task["done"] = True
		self.tasks.remove(task) # Remove from queue
		self.site.onFileDone(task["inner_path"])
		task["evt"].set(True)
		if not self.tasks: self.site.onComplete() # No more task trigger site compelte

